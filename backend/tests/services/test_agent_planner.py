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
    }
