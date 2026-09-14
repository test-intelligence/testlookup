"""E6.1 regression coverage for the deterministic generic reviewer."""
from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.agents.reviewer_agent import ReviewerAgent, review_payload, validate_review_verdict
from app.models.agent_contracts import ReviewVerdictV1


def _contract(agent_name: str) -> dict:
    return {
        "agent_name": agent_name,
        "confidence_score": 80,
        "evidence_count": 0,
        "output_keys": ["analyses"],
    }


def _analysis_payload(**step_overrides) -> dict:
    step = {
        "step_name": "root_cause_analysis",
        "output": {
            "contract": _contract("root_cause_analysis"),
            "analyses": {"tc-1": {"is_flaky": False, "confidence_score": 80}},
        },
        "mode": "shadow",
        "tools_used": [],
        "tool_permissions": {},
    }
    step.update(step_overrides)
    return {
        "reviewed_steps": [step],
        "references": {"test_case_ids": ["tc-1"]},
        "numeric_facts": {},
        "run_data": {},
        "analyses": {"tc-1": {"is_flaky": False, "confidence_score": 80}},
    }


def _check(verdict: ReviewVerdictV1, name: str):
    return next(check for check in verdict.checks if check.name == name)


def test_clean_output_passes_all_deterministic_review_families() -> None:
    verdict = review_payload(_analysis_payload())

    assert verdict.verdict == "pass"
    assert verdict.hallucination_risk == "low"
    assert verdict.requires_human_review is False
    assert {check.family for check in verdict.checks} == {1, 2, 5}
    assert all(check.passed for check in verdict.checks)


@pytest.mark.parametrize(
    ("mutator", "failed_check"),
    [
        (
            lambda body: body["reviewed_steps"][0]["output"].pop("contract"),
            "output_schema_valid",
        ),
        (
            lambda body: body["reviewed_steps"][0]["output"]["analyses"]["tc-1"].update(
                {"test_case_id": "invented"}
            ),
            "references_grounded",
        ),
        (
            lambda body: (
                body.update({"numeric_facts": {"failed_tests": 2}}),
                body["reviewed_steps"][0]["output"]["analyses"]["tc-1"].update(
                    {"narrative": "failed tests: 7"}
                ),
            ),
            "quoted_numbers_match_state",
        ),
    ],
)
def test_family_one_rejects_invalid_schema_ungrounded_ids_and_wrong_numbers(
    mutator, failed_check
) -> None:
    payload = _analysis_payload()
    mutator(payload)

    verdict = review_payload(payload)

    assert verdict.verdict == "reject"
    assert verdict.hallucination_risk == "high"
    assert _check(verdict, failed_check).passed is False


@pytest.mark.parametrize(
    ("reference_key", "reference_bucket"),
    [
        ("test_case_id", "test_case_ids"),
        ("cluster_id", "cluster_ids"),
        ("artifact_id", "artifact_ids"),
    ],
)
def test_family_one_grounds_every_required_identifier_kind(
    reference_key, reference_bucket
) -> None:
    payload = _analysis_payload()
    payload["reviewed_steps"][0]["output"]["analyses"]["tc-1"]["citation"] = {
        reference_key: "known"
    }
    payload["references"][reference_bucket] = ["known"]

    assert _check(review_payload(payload), "references_grounded").passed is True

    payload["reviewed_steps"][0]["output"]["analyses"]["tc-1"]["citation"][
        reference_key
    ] = "invented"
    assert _check(review_payload(payload), "references_grounded").passed is False


def test_family_two_maps_existing_consistency_failures_to_disagreements() -> None:
    payload = _analysis_payload()
    payload["references"]["test_case_ids"].append("tc-missing")

    verdict = review_payload(payload)

    assert verdict.verdict == "pass_with_flags"
    assert verdict.requires_human_review is True
    assert _check(verdict, "coverage_completeness").passed is False
    assert [(item.kind, item.resolution) for item in verdict.disagreements] == [
        ("coverage_completeness", "flag_for_review")
    ]


def test_family_two_adapts_summary_and_release_consistency_checks() -> None:
    summary = {
        "reviewed_steps": [{
            "step_name": "summary",
            "output": {
                "contract": _contract("summary"),
                "structured_summary": {
                    "layer2_incident_view": {"release_impact": "NO_GO"},
                    "layer3_evidence_pack": {},
                    "layer4_action_plan": {},
                },
            },
        }],
        "run_data": {"failed_tests": 0, "pass_rate": 100},
    }
    summary_verdict = review_payload(summary)
    assert _check(summary_verdict, "cross_layer_release_signal").passed is False

    release = {
        "reviewed_steps": [{
            "step_name": "release_risk",
            "output": {
                "release_decision": {
                    "recommendation": "GO",
                    "risk_score": 80,
                    "reasoning": "high risk",
                }
            },
        }]
    }
    release_verdict = review_payload(release)
    assert _check(release_verdict, "recommendation_vs_score").passed is False


@pytest.mark.parametrize(
    ("tools_used", "permissions", "mode", "offender"),
    [
        (["unregistered_tool"], {}, "act", "undeclared:unregistered_tool"),
        (["write_issue"], {"write_issue": "mutating"}, "suggest", "mode:write_issue"),
    ],
)
def test_family_five_rejects_tools_without_policy_or_act_mode(
    tools_used, permissions, mode, offender
) -> None:
    payload = _analysis_payload(
        tools_used=tools_used, tool_permissions=permissions, mode=mode
    )

    verdict = review_payload(payload)

    policy = _check(verdict, "tools_have_declared_policy")
    assert verdict.verdict == "reject"
    assert policy.passed is False
    assert offender in policy.offending_refs


def test_mutating_proposals_are_allowed_below_act_but_executed_actions_are_flagged() -> None:
    payload = _analysis_payload()
    payload["reviewed_steps"][0]["output"]["mutating_proposals"] = [
        {"id": "p-1", "status": "proposed"}
    ]

    assert review_payload(payload).verdict == "pass"

    payload["reviewed_steps"][0]["output"]["mutating_proposals"][0]["status"] = "executed"

    verdict = review_payload(payload)

    assert verdict.verdict == "pass_with_flags"
    assert _check(verdict, "mutating_actions_are_proposals_below_act").passed is False


def test_numeric_grounding_matches_each_label_to_its_own_number() -> None:
    payload = _analysis_payload()
    payload["numeric_facts"] = {"failed_tests": 2, "total_tests": 10}
    payload["reviewed_steps"][0]["output"]["analyses"]["tc-1"]["narrative"] = (
        "2 failed tests out of total tests: 10"
    )

    assert _check(review_payload(payload), "quoted_numbers_match_state").passed is True


@pytest.mark.parametrize(
    "mutation",
    [
        {
            "checks": [
                {
                    "family": family,
                    "name": f"family_{family}",
                    "passed": family != 1,
                    "severity": "blocking",
                    "step_name": "summary",
                }
                for family in (1, 2, 5)
            ]
        },
        {"disagreements": [{"kind": "conflict", "agents": ["a"], "resolution": "reject", "severity": "blocking"}]},
        {"hallucination_risk": "high"},
        {"second_model": {"provider": "ollama", "model": "llama", "agreement_score": 0.69}},
    ],
)
def test_pass_verdict_invariants_reject_contradictory_output(mutation) -> None:
    value = {
        "reviewed_steps": ["summary"],
        "checks": [
            {
                "family": family,
                "name": f"family_{family}",
                "passed": True,
                "severity": "blocking",
                "step_name": "summary",
            }
            for family in (1, 2, 5)
        ],
        "verdict": "pass",
        "disagreements": [],
        "hallucination_risk": "low",
        "requires_human_review": False,
        "second_model": None,
    }
    value.update(mutation)

    with pytest.raises(ValidationError):
        ReviewVerdictV1.model_validate(value)
    assert validate_review_verdict(value).verdict == "reject"


def test_pass_cannot_omit_a_deterministic_check_family() -> None:
    value = {
        "reviewed_steps": ["summary"],
        "checks": [
            {
                "family": family,
                "name": f"family_{family}",
                "passed": True,
                "severity": "blocking",
                "step_name": "summary",
            }
            for family in (1, 2)
        ],
        "verdict": "pass",
        "disagreements": [],
        "hallucination_risk": "low",
        "requires_human_review": False,
    }

    with pytest.raises(ValidationError):
        ReviewVerdictV1.model_validate(value)

    value["verdict"] = "pass_with_flags"
    value["requires_human_review"] = True
    with pytest.raises(ValidationError):
        ReviewVerdictV1.model_validate(value)


def test_malformed_proposal_fails_closed_without_crashing() -> None:
    payload = _analysis_payload()
    payload["reviewed_steps"][0]["output"]["mutating_proposals"] = ["not-an-action"]

    verdict = review_payload(payload)

    assert verdict.verdict == "pass_with_flags"
    assert _check(verdict, "mutating_actions_are_proposals_below_act").offending_refs == [
        "not-an-action"
    ]


def test_non_pass_verdict_always_requires_human_review() -> None:
    with pytest.raises(ValidationError):
        ReviewVerdictV1.model_validate({
            "reviewed_steps": ["summary"], "checks": [], "verdict": "retry",
            "disagreements": [], "hallucination_risk": "medium",
            "requires_human_review": False,
        })


@pytest.mark.asyncio
async def test_reviewer_agent_records_and_returns_the_validated_verdict(monkeypatch) -> None:
    agent = ReviewerAgent()
    calls: list[tuple[str, object]] = []

    async def running(run_id, **_kwargs):
        calls.append(("running", run_id))

    async def decision(run_id, _point, chosen, _rationale, **_kwargs):
        calls.append(("decision", (run_id, chosen)))

    async def done(run_id, **kwargs):
        calls.append(("done", (run_id, kwargs["result_data"]["verdict"])))

    monkeypatch.setattr(agent, "mark_stage_running", running)
    monkeypatch.setattr(agent, "log_decision", decision)
    monkeypatch.setattr(agent, "mark_stage_done", done)

    result = await agent.run({
        "pipeline_run_id": "run-1",
        "reviewer_input": deepcopy(_analysis_payload()),
    })

    assert result["review_verdict"]["verdict"] == "pass"
    assert calls == [
        ("running", "run-1"),
        ("decision", ("run-1", "pass")),
        ("done", ("run-1", "pass")),
    ]
