from __future__ import annotations

import uuid
from hashlib import sha256
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from app.models.postgres import WorkflowDefinition
from app.services import workflow_evaluation_service as svc


PROJECT_ID = uuid.uuid4()


def _definition(agent_id: str = "agent.summary.v1") -> dict:
    return {
        "workflow_id": "wf.custom.eval",
        "version": 1,
        "project_id": str(PROJECT_ID),
        "base": "offline",
        "steps": [{"id": "summary", "agent_id": agent_id, "tools": [], "reviews": []}],
        "edges": [],
        "loops": [],
        "retry_policy": {"max_attempts": 5, "base_seconds": 30, "cap_seconds": 600},
        "review_policy": "human_required",
        "deadline_seconds": 1500,
    }


def _case(index: int, *, status: str = "completed", baseline_passed: bool = True) -> svc.ReplayCase:
    test_run_id = uuid.UUID(int=index + 1)
    prompt_version = f"prompt-{index}"
    input_checksum = sha256(f"input-{index}".encode()).hexdigest()
    return svc.ReplayCase(
        case_id=f"case-{index}",
        project_id=PROJECT_ID,
        test_run_id=test_run_id,
        prompt_version=prompt_version,
        entries=(svc.ReplayEntry(
            agent_id="agent.summary.v1",
            prompt_version=prompt_version,
            input_hash=svc.replay_input_hash(PROJECT_ID, input_checksum, "summary"),
            output={"summary": "measured"},
            step_id="summary",
            input_checksum_sha256=input_checksum,
            stage_status=status,
            degraded=status != "completed",
            cost_usd=0.01,
            latency_ms=10,
        ),),
        baseline_plan_passed=baseline_passed,
        baseline_degraded=False,
        reviewer_rejected=False,
        baseline_cost_usd=0.01,
        baseline_latency_ms=10,
    )


def _evaluate(cases, *, agent_id: str = "agent.summary.v1") -> dict:
    return svc.evaluate_replay(
        project_id=PROJECT_ID,
        workflow_id="wf.custom.eval",
        version=1,
        definition=_definition(agent_id),
        cases=cases,
    )


def test_exact_replay_keys_produce_full_coverage_and_pass() -> None:
    result = _evaluate([_case(index) for index in range(20)])

    assert result["verdict"] == "pass"
    assert result["coverage"] == 1.0
    assert result["measured_steps"] == result["expected_steps"] == 20
    assert all(
        case["steps"][0]["measurement"] == "replayed" for case in result["cases"]
    )


def test_cache_miss_is_unmeasured_and_never_reuses_another_agent_output() -> None:
    result = _evaluate(
        [_case(index) for index in range(20)],
        agent_id="agent.root_cause.v1",
    )

    assert result["verdict"] == "insufficient_samples"
    assert result["coverage"] == 0.0
    assert result["cases"][0]["steps"][0]["measurement"] == "unmeasured"


def test_changed_execution_authority_is_publish_blocking() -> None:
    case = _case(0)
    historical = "a" * 64
    candidate = "b" * 64
    entry = case.entries[0]
    case = svc.ReplayCase(
        **{
            **case.__dict__,
            "entries": (
                svc.ReplayEntry(
                    **{
                        **entry.__dict__,
                        "step_id": "summary",
                        "authority_sha256": historical,
                        "input_hash": svc.replay_input_hash(
                            PROJECT_ID,
                            entry.input_checksum_sha256,
                            "summary",
                            historical,
                        ),
                    }
                ),
            ),
        }
    )

    result = svc.evaluate_replay(
        project_id=PROJECT_ID,
        workflow_id="wf.custom.eval",
        version=1,
        definition=_definition(),
        cases=[case],
        candidate_step_authority={"summary": candidate},
    )

    assert result["verdict"] == "fail"
    assert result["authority_mismatch_steps"] == 1
    assert result["cases"][0]["steps"][0]["reason"] == "execution_authority_mismatch"


@pytest.mark.parametrize(
    "change",
    ["tools", "model", "topology", "config", "endpoint", "prompt", "runtime", "plan", "policy"],
)
def test_step_authority_covers_every_behavior_input(change: str) -> None:
    definition = _definition()
    configs = {"agent.summary.v1": {"config": {"model": {"tier": "slm"}}}}
    prompts = {"summary_system": "v1:abc"}
    runtime = {"workflow": "abc"}
    endpoints = {"agent.summary.v1": "endpoint-a"}
    planning = {"run_budget": {"max_llm_calls": 1}}
    baseline = svc.step_execution_authority(
        definition=definition,
        step_id="summary",
        resolved_agent_configs=configs,
        endpoint_authority_fingerprints=endpoints,
        prompt_versions=prompts,
        runtime_versions=runtime,
        workflow_plan_sha256="a" * 64,
        workflow_runtime_authority_sha256="b" * 64,
        planning_context=planning,
    )
    if change == "tools":
        definition["steps"][0]["tools"] = ["list_run_failures"]
    elif change == "model":
        definition["steps"][0]["model"] = {"tier": "llm"}
    elif change == "topology":
        definition["edges"] = [{"from": "summary", "to": "__end__"}]
    elif change == "config":
        configs["agent.summary.v1"]["config"]["model"]["tier"] = "llm"
    elif change == "endpoint":
        endpoints["agent.summary.v1"] = "endpoint-b"
    elif change == "prompt":
        prompts["summary_system"] = "v2:def"
    elif change == "plan":
        planning = {**planning, "plan_changed": True}
    elif change == "policy":
        planning["run_budget"]["max_llm_calls"] = 2
    else:
        runtime["workflow"] = "def"

    changed = svc.step_execution_authority(
        definition=definition,
        step_id="summary",
        resolved_agent_configs=configs,
        endpoint_authority_fingerprints=endpoints,
        prompt_versions=prompts,
        runtime_versions=runtime,
        workflow_plan_sha256=("c" * 64 if change == "plan" else "a" * 64),
        workflow_runtime_authority_sha256="b" * 64,
        planning_context=planning,
    )
    assert changed != baseline


def test_replay_hash_uses_recorded_input_not_test_run_identity() -> None:
    first = sha256(b"attempt-one").hexdigest()
    second = sha256(b"attempt-two").hexdigest()

    assert svc.replay_input_hash(PROJECT_ID, first, "summary") != svc.replay_input_hash(
        PROJECT_ID, second, "summary"
    )


def _stage(*, attempt: int, input_value: str, status: str = "completed"):
    pipeline_run_id = uuid.uuid4()
    checkpoint = {"summary": input_value}
    output_checksum = svc._checksum(checkpoint)
    attempt_key = svc._checksum({
        "pipeline_run_id": str(pipeline_run_id),
        "stage_name": "summary",
        "attempt": attempt,
        "output_checksum_sha256": output_checksum,
    })
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        pipeline_run_id=pipeline_run_id,
        stage_name="summary",
        attempt=attempt,
        status=status,
        started_at=now,
        completed_at=now,
        checkpoint_data=checkpoint,
        result_data={
            "_replay": {
                "input_checksum_sha256": sha256(input_value.encode()).hexdigest(),
                "output_checksum_sha256": output_checksum,
                "runtime_versions": {"workflow": "v1"},
                "attempt": attempt,
                "attempt_idempotency_key": attempt_key,
            }
        },
    )


def test_replay_receipt_is_required_and_tamper_evident() -> None:
    stage = _stage(attempt=2, input_value="exact input")
    assert svc._stage_replay_receipt(stage) is not None

    stage.checkpoint_data = {"summary": "tampered"}
    assert svc._stage_replay_receipt(stage) is None
    stage.result_data = {}
    assert svc._stage_replay_receipt(stage) is None


def test_terminal_latest_stage_attempt_wins_deterministically() -> None:
    terminal = _stage(attempt=2, input_value="terminal")
    pending = _stage(attempt=3, input_value="pending", status="running")

    assert max((pending, terminal), key=svc._stage_selection_key) is terminal


def test_manifest_checksum_binds_replay_evidence() -> None:
    first = _evaluate([_case(index) for index in range(20)])
    changed_case = _case(0)
    changed_entry = changed_case.entries[0]
    changed_case = svc.ReplayCase(**{
        **changed_case.__dict__,
        "entries": (
            svc.ReplayEntry(**{
                **changed_entry.__dict__,
                "output": {"summary": "changed"},
                "output_checksum_sha256": sha256(b"changed").hexdigest(),
            }),
        ),
    })
    second = _evaluate([changed_case, *[_case(index) for index in range(1, 20)]])

    assert first["manifest"]["corpus_evidence_sha256"] != second["manifest"]["corpus_evidence_sha256"]
    assert first["manifest_checksum"] != second["manifest_checksum"]


def test_measured_plan_and_degradation_regressions_fail_g4() -> None:
    result = _evaluate([
        _case(index, status="failed", baseline_passed=True) for index in range(20)
    ])

    assert result["verdict"] == "fail"
    assert {regression["metric"] for regression in result["regressions"]} == {
        "plan_verification_pass_rate",
        "degraded_rate",
    }


def test_fewer_than_twenty_runs_is_insufficient_even_with_full_coverage() -> None:
    result = _evaluate([_case(index) for index in range(19)])

    assert result["verdict"] == "insufficient_samples"
    assert result["coverage"] == 1.0
    assert "19 runs" in result["reason"]


def test_evaluation_uses_project_base_corpus_lock() -> None:
    first = svc.replay_corpus_lock_key(PROJECT_ID, "offline")
    second_workflow_same_corpus = svc.replay_corpus_lock_key(PROJECT_ID, "offline")
    other_base = svc.replay_corpus_lock_key(PROJECT_ID, "deep")

    assert first == second_workflow_same_corpus
    assert first != other_base
    assert "wf.custom" not in first


def test_replay_never_claims_to_measure_control_flow_from_cached_step_outputs() -> None:
    definition = _definition()
    definition["edges"] = [{
        "from": "summary",
        "to": "__end__",
        "when": {"field": "fallback_used", "op": "is_true"},
    }]

    result = svc.evaluate_replay(
        project_id=PROJECT_ID,
        workflow_id="wf.custom.eval",
        version=1,
        definition=definition,
        cases=[_case(index) for index in range(20)],
    )

    assert result["coverage"] == 1.0
    assert result["topology_measured"] is False
    assert result["verdict"] == "insufficient_samples"
    assert "does not measure branch" in result["reason"]


def _row(verdict: str) -> WorkflowDefinition:
    now = datetime.now(timezone.utc)
    return WorkflowDefinition(
        id=uuid.uuid4(),
        project_id=PROJECT_ID,
        workflow_id="wf.custom.eval",
        version=1,
        name="Evaluated workflow",
        base="offline",
        definition=_definition(),
        status="draft",
        eval_verdict=verdict,
        eval_regression_accepted=False,
        created_at=now,
        updated_at=now,
    )


def test_publish_refuses_failed_g4_without_explicit_reasoned_acceptance() -> None:
    row = _row("fail")
    with pytest.raises(svc.WorkflowEvaluationConflict, match="accept_regression=true"):
        svc.enforce_publish_gate(
            row,
            accept_regression=False,
            reason=None,
            accepted_by=uuid.uuid4(),
        )

    actor_id = uuid.uuid4()
    svc.enforce_publish_gate(
        row,
        accept_regression=True,
        reason="Known latency tradeoff for the release workflow",
        accepted_by=actor_id,
    )
    assert row.eval_regression_accepted is True
    assert row.eval_regression_reason == "Known latency tradeoff for the release workflow"
    assert row.eval_regression_accepted_by == actor_id
    assert row.eval_regression_accepted_at is not None


@pytest.mark.parametrize("verdict", ["pass", "insufficient_samples", None])
def test_publish_allows_non_regressing_or_unmeasured_definitions(verdict: str | None) -> None:
    row = _row(verdict or "pass")
    row.eval_verdict = verdict
    svc.enforce_publish_gate(
        row,
        accept_regression=False,
        reason=None,
        accepted_by=uuid.uuid4(),
    )
