from app.services.agent_planner import build_workflow_plan, verify_workflow_execution


def test_all_green_offline_plan_skips_failure_only_stages():
    plan = build_workflow_plan(
        workflow_type="offline",
        failed_test_ids=[],
        analyses={},
        threshold=80,
    )

    by_stage = {stage["stage"]: stage for stage in plan["stages"]}

    assert by_stage["ingestion"]["planned"] is True
    assert by_stage["summary"]["planned"] is True
    assert by_stage["anomaly_detection"]["planned"] is False
    assert by_stage["root_cause_analysis"]["planned"] is False
    assert by_stage["triage"]["planned"] is False
    assert plan["triageable_test_ids"] == []


def test_plan_triage_is_deterministic_and_threshold_aware():
    plan = build_workflow_plan(
        workflow_type="offline",
        failed_test_ids=["tc-b", "tc-a"],
        analyses={
            "tc-b": {"confidence_score": 91, "is_flaky": False},
            "tc-a": {"confidence_score": 99, "is_flaky": True},
            "tc-c": {"confidence_score": 20, "is_flaky": False},
        },
        threshold=80,
    )

    by_stage = {stage["stage"]: stage for stage in plan["stages"]}

    assert plan["failure_count"] == 2
    assert plan["triageable_test_ids"] == ["tc-b"]
    assert by_stage["triage"]["planned"] is True


def test_verifier_accepts_explained_all_green_skip():
    plan = build_workflow_plan(
        workflow_type="offline",
        failed_test_ids=[],
        analyses={},
        threshold=80,
    )
    verification = verify_workflow_execution(
        plan,
        {
            "completed_stages": ["ingestion", "summary", "root_cause_analysis"],
            "skipped_stages": ["root_cause_analysis"],
            "analyses": {},
            "_workflow_route_decisions": [
                {
                    "decision_point": "route_after_ingestion",
                    "chosen": "summary",
                    "rationale": "no failed tests",
                }
            ],
        },
    )

    assert verification["status"] == "passed"
    assert {check["name"] for check in verification["checks"]} == {
        "required_planned_stages_completed",
        "unplanned_stages_not_executed",
        "route_rationale_persisted",
        "all_green_skips_analysis_work",
        "contract_evidence_support",
        "summary_provenance_present",
        "mutating_actions_policy_aligned",
        "release_decision_policy_trace",
    }


def test_verifier_warns_when_summary_lacks_provenance():
    plan = build_workflow_plan(
        workflow_type="offline",
        failed_test_ids=["tc-1"],
        analyses={"tc-1": {"confidence_score": 90}},
        threshold=80,
    )
    verification = verify_workflow_execution(
        plan,
        {
            "completed_stages": ["ingestion", "root_cause_analysis", "summary"],
            "skipped_stages": [],
            "analyses": {"tc-1": {"confidence_score": 90}},
            "structured_summary": {"layer1_executive_summary": "summary"},
            "summary_provenance": {},
            "agent_contracts": {
                "root_cause_analysis": {
                    "evidence_refs": [{"type": "analysis", "id": "tc-1"}],
                    "decision_reason": "root_cause_analysis_completed",
                },
                "summary": {
                    "evidence_refs": [{"type": "summary_provenance", "id": "missing"}],
                    "decision_reason": "structured_summary_generated",
                },
            },
            "_workflow_route_decisions": [],
        },
    )

    check = next(c for c in verification["checks"] if c["name"] == "summary_provenance_present")
    assert verification["status"] == "warning"
    assert check["status"] == "warn"
    assert "context_sha256" in check["details"]["missing"]


def test_verifier_fails_unapproved_mutating_triage_action():
    plan = build_workflow_plan(
        workflow_type="offline",
        failed_test_ids=["tc-1"],
        analyses={"tc-1": {"confidence_score": 90}},
        threshold=80,
    )
    verification = verify_workflow_execution(
        plan,
        {
            "completed_stages": ["ingestion", "root_cause_analysis", "summary", "triage"],
            "skipped_stages": [],
            "analyses": {"tc-1": {"confidence_score": 90}},
            "triage_results": [
                {
                    "test_case_id": "tc-1",
                    "mutating_action": "jira_ticket_creation",
                    "approval_status": "executed",
                    "requires_approval": False,
                }
            ],
            "agent_contracts": {
                "root_cause_analysis": {
                    "evidence_refs": [{"type": "analysis", "id": "tc-1"}],
                    "decision_reason": "root_cause_analysis_completed",
                },
                "triage": {
                    "evidence_refs": [{"type": "triage_result", "id": "tc-1"}],
                    "decision_reason": "triage_completed",
                },
            },
            "_workflow_route_decisions": [],
        },
    )

    check = next(c for c in verification["checks"] if c["name"] == "mutating_actions_policy_aligned")
    assert verification["status"] == "failed"
    assert check["status"] == "fail"
    assert check["details"]["violations"][0]["test_case_id"] == "tc-1"


def test_verifier_accepts_release_decision_with_policy_trace():
    plan = build_workflow_plan(
        workflow_type="deep",
        failed_test_ids=["tc-1"],
        analyses={"tc-1": {"confidence_score": 90}},
        threshold=80,
    )
    verification = verify_workflow_execution(
        plan,
        {
            "completed_stages": ["ingestion", "summary", "release_risk"],
            "skipped_stages": [],
            "analyses": {"tc-1": {"confidence_score": 90}},
            "release_decision": {
                "recommendation": "CONDITIONAL_GO",
                "risk_score": 42,
                "score_model_version": "criticality:v1",
                "policy_id": "policy-1",
                "policy_evaluation": {"policy_version": 3},
            },
            "agent_contracts": {
                "root_cause_analysis": {
                    "evidence_refs": [{"type": "analysis", "id": "tc-1"}],
                    "decision_reason": "root_cause_analysis_completed",
                },
                "release_risk": {
                    "evidence_refs": [{"type": "score_model", "id": "criticality:v1"}],
                    "decision_reason": "Deterministic release score",
                },
            },
            "_workflow_route_decisions": [],
        },
    )

    check = next(c for c in verification["checks"] if c["name"] == "release_decision_policy_trace")
    assert check["status"] == "pass"
