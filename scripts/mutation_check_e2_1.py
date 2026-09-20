"""Prove E2.1's step-tracing tests kill telemetry contract mutations."""
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
    "backend/tests/services/test_agent_step_tracing.py",
    "backend/tests/test_decision_evidence_checkpoint_resume.py",
)

# (file, correct source, mutation, expected replacement count).  Counts are
# asserted before every run so a refactor cannot turn the check into a no-op.
MUTATIONS = (
    (
        "backend/app/services/agent_step_tracing.py",
        '"testlookup.agent_id": capability_id,',
        '"testlookup.agent": capability_id,',
        1,
    ),
    (
        "backend/app/services/agent_step_tracing.py",
        '"gen_ai.request.model", observation.model',
        '"gen_ai.request.name", observation.model',
        1,
    ),
    (
        "backend/app/services/agent_step_tracing.py",
        '"gen_ai.usage.input_tokens", observation.input_tokens',
        '"gen_ai.usage.prompt_tokens", observation.input_tokens',
        1,
    ),
    (
        "backend/app/services/agent_step_tracing.py",
        '"gen_ai.usage.output_tokens", observation.output_tokens',
        '"gen_ai.usage.completion_tokens", observation.output_tokens',
        1,
    ),
    (
        "backend/app/services/agent_step_tracing.py",
        '"tier": tier,',
        '"model_tier": tier,',
        1,
    ),
    (
        "backend/app/services/agent_step_tracing.py",
        '"attempt": attempt,',
        '"attempt_no": attempt,',
        1,
    ),
    (
        "backend/app/services/agent_step_tracing.py",
        "observation.llm_calls += 1",
        "observation.llm_calls += 0",
        1,
    ),
    (
        "backend/app/services/agent_step_tracing.py",
        "observation.input_tokens += _positive_int(input_tokens)",
        "observation.input_tokens += 0",
        1,
    ),
    (
        "backend/app/agents/workflow.py",
        "with trace_agent_step(stage_name, state):",
        "if False:",
        1,
    ),
    (
        "backend/app/agents/workflow.py",
        '"_attempt": int(pipeline_setup.get("attempt") or 1),',
        '"_attempt": 1,',
        2,
    ),
    (
        "backend/app/agents/workflow.py",
        '"attempt": current_row_attempt + 1,',
        '"attempt": 1,',
        1,
    ),
    (
        "backend/app/services/llm_policy_service.py",
        "record_gen_ai_request(provider=traced_provider, model=traced_model)",
        'record_gen_ai_request(provider=traced_provider, model="deterministic")',
        1,
    ),
    (
        "backend/app/services/pipeline_budget_service.py",
        'input_tokens=delta if key == "observed_input_tokens" else 0,',
        "input_tokens=0,",
        1,
    ),
    (
        "infra/monitoring/grafana/dashboards/testlookup-overview.json",
        '"operation": "testlookup.agent.step"',
        '"operation": "testlookup.agent"',
        2,
    ),
    (
        "infra/monitoring/grafana/provisioning/datasources/prometheus.yml",
        "    uid: jaeger\n    type: jaeger",
        "    uid: generated\n    type: jaeger",
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
    print(f"E2.1 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
