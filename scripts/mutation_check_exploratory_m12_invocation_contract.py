"""Prove M12 invocation regressions kill the unsafe behavior they cover."""
from __future__ import annotations

import subprocess
import sys
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
        "cancelled-retry-guard",
        "backend/app/routers/agent_invoke.py",
        'if bool(getattr(pipeline, "cancel_requested", False)) or str(\n'
        '            getattr(pipeline, "error", "") or ""\n'
        '        ).startswith(CANCELLED_ERROR_PREFIX):',
        "if False:",
        "backend/tests/test_agent_invocation_retry_cancel.py::test_retry_refuses_cancelled_invocation_without_side_effects",
    ),
    Mutation(
        "manual-retry-row-lock",
        "backend/app/routers/agent_invoke.py",
        "pipeline = await _load_pipeline(db, invocation, for_update=True)",
        "pipeline = await _load_pipeline(db, invocation)",
        "backend/tests/test_agent_invocation_retry_cancel.py::test_retry_resumes_the_same_run_after_the_commit",
    ),
    Mutation(
        "lost-dispatch-invocation-lock",
        "backend/app/routers/agent_invoke.py",
        "invocation = await _load_invocation_or_404(db, invocation_id, for_update=True)\n"
        "    pipeline = await _load_pipeline(db, invocation, for_update=True)",
        "invocation = await _load_invocation_or_404(db, invocation_id)\n"
        "    pipeline = await _load_pipeline(db, invocation, for_update=True)",
        "backend/tests/integration/test_pipeline_cancel_retry_postgres.py::"
        "test_concurrent_lost_dispatch_retries_enqueue_once",
    ),
    Mutation(
        "queued-cancel-intent",
        "backend/app/routers/agent_invoke.py",
        "        invocation.cancel_requested = True\n",
        "        invocation.cancel_requested = False\n",
        "backend/tests/test_agent_invocation_retry_cancel.py::test_cancel_before_the_run_exists_persists_terminal_intent",
    ),
    Mutation(
        "pipeline-create-cancel-recheck",
        "backend/app/agents/workflow.py",
        "if invocation is not None and bool(invocation.cancel_requested):",
        "if False and invocation is not None and bool(invocation.cancel_requested):",
        "backend/tests/test_agent_invocations.py::test_pipeline_creation_rechecks_queued_cancellation_under_the_invocation_lock",
    ),
    Mutation(
        "worker-queued-cancel-guard",
        "backend/app/worker/tasks.py",
        'if loaded["cancel_requested"]:',
        'if False and loaded["cancel_requested"]:',
        "backend/tests/test_agent_invocations.py::test_the_worker_does_not_start_an_invocation_cancelled_while_queued",
    ),
    Mutation(
        "manual-retry-durable-admission",
        "backend/app/routers/agent_invoke.py",
        'apply_transition(pipeline, "retry_wait", error=pipeline.error)',
        'apply_transition(pipeline, "failed", error=pipeline.error)',
        "backend/tests/test_agent_invocation_retry_cancel.py::test_retry_resumes_the_same_run_after_the_commit",
    ),
    Mutation(
        "manual-retry-attempt-fence",
        "backend/app/routers/agent_invoke.py",
        '"expected_attempt": int(pipeline.attempt or 1) + 1,',
        '"expected_attempt": None,',
        "backend/tests/test_agent_invocation_retry_cancel.py::test_retry_resumes_the_same_run_after_the_commit",
    ),
    Mutation(
        "subject-row-lock",
        "backend/app/routers/agent_invoke.py",
        "select(TestRun).where(TestRun.id == test_run_id).with_for_update()",
        "select(TestRun).where(TestRun.id == test_run_id)",
        "backend/tests/test_agent_invocation_idempotency.py::test_a_first_request_stores_its_key_commits_marks_the_key_done_then_dispatches",
    ),
    Mutation(
        "keyed-active-conflict",
        "backend/app/routers/agent_invoke.py",
        "if idempotency_key is not None:\n                raise HTTPException(",
        "if False and idempotency_key is not None:\n                raise HTTPException(",
        "backend/tests/test_agent_invocation_idempotency.py::test_an_explicit_key_does_not_silently_adopt_an_unrelated_active_invocation",
    ),
    Mutation(
        "durable-project-scope",
        "backend/app/routers/agent_invoke.py",
        "                AgentInvocation.project_id == project_id,\n",
        "",
        "backend/tests/test_agent_invocation_idempotency.py::test_the_replay_lookup_is_scoped_to_the_user_and_refuses_a_different_request",
    ),
    Mutation(
        "durable-agent-scope",
        "backend/app/routers/agent_invoke.py",
        "                AgentInvocation.agent_id == agent_id,\n",
        "",
        "backend/tests/test_agent_invocation_idempotency.py::test_the_replay_lookup_is_scoped_to_the_user_and_refuses_a_different_request",
    ),
    Mutation(
        "downgrade-idempotency-collision-collapse",
        "backend/migrations/versions/0190_agent_invocation_idempotency_scope.py",
        "            SET idempotency_key = NULL\n",
        "            SET idempotency_key = idempotency_key\n",
        "backend/tests/integration/test_pipeline_cancel_retry_postgres.py::"
        "test_idempotency_scope_migration_really_downgrades_after_scoped_use",
    ),
    Mutation(
        "zero-wait-no-sleep",
        "backend/app/routers/agent_invoke.py",
        "while True:\n        view = await _invocation_view(db, invocation)",
        "while True:\n        await sleep(poll_interval)\n        view = await _invocation_view(db, invocation)",
        "backend/tests/test_agent_invocation_sync_sse.py::test_a_zero_length_sync_wait_reads_once_without_sleeping",
    ),
    Mutation(
        "ticket-collision-regeneration",
        "backend/app/services/invocation_stream.py",
        "if await redis.set(_key(ticket), payload, ex=ttl, nx=True):",
        "if True or await redis.set(_key(ticket), payload, ex=ttl, nx=True):",
        "backend/tests/test_agent_invocation_sync_sse.py::test_ticket_issue_regenerates_after_a_token_collision",
    ),
    Mutation(
        "eventsource-public-router",
        "backend/app/bootstrap.py",
        "    agent_invoke.stream_router,\n",
        "",
        "backend/tests/test_agent_invocation_sync_sse.py::test_a_ticket_reaches_the_mounted_eventsource_without_auth_headers",
    ),
    Mutation(
        "sse-observable-change",
        "backend/app/routers/agent_invoke.py",
        'key = json.dumps(payload, sort_keys=True, separators=(",", ":"))',
        'key = str(payload["status"])',
        "backend/tests/test_agent_invocation_sync_sse.py::test_the_stream_emits_when_an_observable_error_changes",
    ),
    Mutation(
        "openapi-sync-capacity",
        "backend/app/routers/agent_invoke.py",
        '        503: {\n            "description": "Synchronous invocation capacity is full; Retry-After is returned",',
        '        504: {\n            "description": "Synchronous invocation capacity is full; Retry-After is returned",',
        "backend/tests/test_agent_invocation_idempotency.py::test_openapi_documents_every_bounded_invoke_outcome",
    ),
)


def run_test(test: str, suffix: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:testlookup",
            "--basetemp",
            f".pytest-tmp-exploratory-m12-mutation-{suffix}",
            test,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    originals: dict[Path, bytes] = {}
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        originals.setdefault(path, path.read_bytes())
        source = originals[path].decode("utf-8")
        if source.count(mutation.safe) != 1:
            raise AssertionError(f"{mutation.name} mutation must apply exactly once")
        baseline = run_test(mutation.test, f"baseline-{mutation.name}")
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
            mutated = run_test(mutation.test, mutation.name)
            if mutated.returncode != 1:
                raise AssertionError(
                    f"{mutation.name} was not killed with pytest exit 1\n"
                    f"{mutated.stdout}{mutated.stderr}"
                )
        finally:
            path.write_bytes(originals[path])
    print(f"M12 mutation check passed: {len(MUTATIONS)} unsafe changes were killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
