"""Prove E2.2's emission tests kill agent-metric contract mutations."""
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
    "backend/tests/services/test_agent_metrics_contract.py",
    "backend/tests/services/test_agent_step_tracing.py",
    "backend/tests/services/test_workflow_run_state.py",
    "backend/tests/services/test_review_request_service.py",
    "backend/tests/test_reviews_api.py",
    "backend/tests/test_root_cause_tier_routing.py",
    "backend/tests/test_summary_tier_routing.py",
    "backend/tests/services/test_llm_circuit_breaker.py",
)

# (file, correct source, mutation, expected replacement count). Replacement
# counts are checked first so a refactor cannot silently turn this into a pass.
MUTATIONS = (
    (
        "backend/app/core/metrics.py",
        '"testlookup_agent_invocations_total",',
        '"testlookup_agent_invocation_total",',
        1,
    ),
    (
        "backend/app/services/agent_step_tracing.py",
        "status=observation.outcome,",
        'status="success",',
        1,
    ),
    (
        "backend/app/services/workflow_run_state.py",
        "_record_transition(current, target, run=run, error=error)",
        "_record_transition(current, current, run=run, error=error)",
        1,
    ),
    (
        "backend/app/services/workflow_run_state.py",
        "_record_transition(expected_s, target, error=error)",
        "_record_transition(expected_s, expected_s, error=error)",
        1,
    ),
    (
        "backend/app/services/workflow_run_state.py",
        'startswith("Worker stopped heartbeating")',
        'startswith("never a lease failure")',
        1,
    ),
    (
        "backend/app/services/review_request_service.py",
        'review_requests_total.labels(state="pending_review").inc()',
        'review_requests_total.labels(state="accepted").inc()',
        1,
    ),
    (
        "backend/app/services/review_request_service.py",
        'review_requests_total.labels(state="superseded").inc(len(older))',
        'review_requests_total.labels(state="pending_review").inc(len(older))',
        1,
    ),
    (
        "backend/app/services/review_request_service.py",
        "review_requests_total.labels(state=decision).inc()",
        'review_requests_total.labels(state="pending_review").inc()',
        1,
    ),
    (
        "backend/app/services/analysis_router.py",
        '**{"from": choice.tier, "to": decision.choice.tier},',
        '**{"from": decision.choice.tier, "to": decision.choice.tier},',
        1,
    ),
    (
        "backend/app/agents/summary_agent.py",
        '**{"from": choice.tier, "to": decision.choice.tier},',
        '**{"from": decision.choice.tier, "to": decision.choice.tier},',
        1,
    ),
    (
        "backend/app/services/llm_circuit_breaker.py",
        "_STATE_OPEN: 1.0",
        "_STATE_OPEN: 0.0",
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
            raise AssertionError(
                f"mutation did not change source: {relative}: {good!r}"
            )
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
    print(f"E2.2 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
