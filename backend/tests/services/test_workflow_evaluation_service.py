from __future__ import annotations

import uuid
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
    return svc.ReplayCase(
        case_id=f"case-{index}",
        project_id=PROJECT_ID,
        test_run_id=test_run_id,
        prompt_version=prompt_version,
        entries=(svc.ReplayEntry(
            agent_id="agent.summary.v1",
            prompt_version=prompt_version,
            input_hash=svc.replay_input_hash(PROJECT_ID, test_run_id, "summary"),
            output={"summary": "measured"},
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
