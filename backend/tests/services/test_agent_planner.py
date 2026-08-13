from copy import deepcopy
import uuid

from app.services.agent_planner import (
    attach_workflow_plan_and_verification,
    build_cluster_investigation_plan,
    build_workflow_plan,
    cluster_investigation_plan_integrity_status,
    compute_cluster_scope_sha256,
    verify_workflow_execution,
)


def _cluster(cluster_id: str, member_count: int = 1) -> dict:
    return {
        "failure_cluster_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"fc:{cluster_id}")),
        "cluster_id": cluster_id,
        "member_test_ids": [
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"member:{cluster_id}:{index}"))
            for index in range(member_count)
        ],
    }


def _cluster_plan(clusters: list[dict], **kwargs) -> dict:
    return build_cluster_investigation_plan(
        parent_pipeline_run_id="11111111-1111-1111-1111-111111111111",
        project_id="22222222-2222-2222-2222-222222222222",
        run_id="33333333-3333-3333-3333-333333333333",
        clusters=clusters,
        aggregate_budget={
            "max_llm_calls": 5,
            "max_tokens": 101,
            "max_cost_usd": 0.100001,
            "max_seconds": 61,
        },
        max_children=kwargs.get("max_children", 2),
        max_members=kwargs.get("max_members", 50),
    )


def test_cluster_expansion_is_permutation_deterministic_and_ranked():
    clusters = [_cluster("cl_b", 2), _cluster("cl_a", 2), _cluster("cl_c", 1)]
    forward = _cluster_plan(clusters)
    reverse = _cluster_plan(list(reversed(clusters)))

    assert forward == reverse
    assert [task["cluster_id"] for task in forward["selected"]] == ["cl_a", "cl_b"]
    assert forward["skipped"][0]["skip_reason"] == "max_children_reached"
    assert cluster_investigation_plan_integrity_status(forward)[0] == "verified"

    member_reordered = deepcopy(clusters)
    member_reordered[0]["member_test_ids"].reverse()
    assert _cluster_plan(member_reordered) == forward


def test_cluster_scope_hash_canonicalizes_member_order():
    cluster = _cluster("cl_hash", 2)
    args = (
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
        "11111111-1111-1111-1111-111111111111",
        cluster["failure_cluster_id"],
    )
    assert compute_cluster_scope_sha256(*args, cluster["member_test_ids"]) == (
        compute_cluster_scope_sha256(
            *args, reversed(cluster["member_test_ids"])
        )
    )


def test_cluster_expansion_allocations_do_not_exceed_aggregate_caps():
    plan = _cluster_plan([_cluster("cl_a", 3), _cluster("cl_b", 2)])
    budgets = [task["budget"] for task in plan["selected"]]

    assert sum(item["max_llm_calls"] for item in budgets) <= 5
    assert sum(item["max_tokens"] for item in budgets) <= 101
    assert sum(item["max_cost_usd"] for item in budgets) <= 0.100001
    assert sum(item["max_seconds"] for item in budgets) <= 61


def test_cluster_expansion_preserves_explicit_zero_budget():
    plan = build_cluster_investigation_plan(
        parent_pipeline_run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        clusters=[_cluster("cl_zero")],
        aggregate_budget={
            "max_llm_calls": 0,
            "max_tokens": 0,
            "max_cost_usd": 0,
            "max_seconds": 0,
        },
    )

    assert plan["aggregate_budget"] == {
        "max_llm_calls": 0,
        "max_tokens": 0,
        "max_cost_usd": 0.0,
        "max_seconds": 0,
    }
    assert plan["selected"] == []
    assert plan["skipped"][0]["skip_reason"] == (
        "aggregate_time_budget_exhausted"
    )


def test_cluster_expansion_never_selects_zero_time_children():
    plan = build_cluster_investigation_plan(
        parent_pipeline_run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        clusters=[_cluster(f"cl_{index}") for index in range(4)],
        aggregate_budget={
            "max_llm_calls": 4,
            "max_tokens": 400,
            "max_cost_usd": 1.0,
            "max_seconds": 1,
        },
        max_children=4,
    )

    assert len(plan["selected"]) == 1
    assert plan["selected"][0]["budget"]["max_seconds"] == 1
    assert {
        item["skip_reason"] for item in plan["skipped"]
    } == {"aggregate_time_budget_exhausted"}


def test_cluster_expansion_skips_duplicate_overlap_oversize_and_malformed():
    duplicate_a = _cluster("cl_duplicate", 1)
    duplicate_b = {**_cluster("cl_duplicate", 1)}
    duplicate_b["failure_cluster_id"] = str(uuid.uuid4())
    overlap_a = _cluster("cl_overlap_a", 1)
    overlap_b = _cluster("cl_overlap_b", 1)
    overlap_b["member_test_ids"] = list(overlap_a["member_test_ids"])
    oversize = _cluster("cl_large", 3)
    duplicate_member = _cluster("cl_duplicate_member", 1)
    duplicate_member["member_test_ids"] *= 2
    malformed = {"cluster_id": "cl_missing_authority", "member_test_ids": []}

    plan = _cluster_plan(
        [
            duplicate_a, duplicate_b, overlap_a, overlap_b, oversize,
            duplicate_member, malformed,
        ],
        max_children=10,
        max_members=2,
    )
    reasons = {item["skip_reason"] for item in plan["skipped"]}

    assert plan["selected"] == []
    assert {
        "duplicate_cluster_id",
        "overlapping_member_test_ids",
        "member_limit_exceeded",
        "duplicate_member_test_ids",
        "candidate_malformed",
    }.issubset(reasons)


def test_cluster_expansion_hash_detects_tampering():
    plan = _cluster_plan([_cluster("cl_a")])
    tampered = deepcopy(plan)
    tampered["selected"][0]["budget"]["max_tokens"] += 1

    status, actual = cluster_investigation_plan_integrity_status(tampered)
    assert status == "failed"
    assert actual != plan["expansion_sha256"]


def test_explained_deep_plan_preserves_cluster_gate_and_budget_snapshot():
    budget = {
        "max_llm_calls": 2,
        "max_tokens": 500,
        "max_cost_usd": 0.25,
        "max_seconds": 120,
    }
    initial = build_workflow_plan(
        workflow_type="deep",
        cluster_children_enabled=True,
        cluster_children_aggregate_budget=budget,
    )
    state = attach_workflow_plan_and_verification(
        {
            "initial_workflow_plan": initial,
            "failed_test_ids": ["tc-1"],
            "analyses": {},
            "completed_stages": [],
            "skipped_stages": [],
        },
        workflow_type="deep",
    )

    explained = state["workflow_plan"]
    by_stage = {item["stage"]: item for item in explained["stages"]}
    assert explained["cluster_children_enabled"] is True
    assert explained["cluster_children_aggregate_budget"] == budget
    assert by_stage["cluster_investigation_dispatch"]["planned"] is True
    assert by_stage["cluster_investigation_join"]["planned"] is True


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
        "terminal_decision_verification_passed",
    }


def test_deep_verifier_fails_when_required_terminal_critic_rejects_report():
    plan = build_workflow_plan(workflow_type="deep", failed_test_ids=[], analyses={})
    completed = [stage["stage"] for stage in plan["stages"] if stage["planned"]]
    verification = verify_workflow_execution(
        plan,
        {
            "completed_stages": completed,
            "skipped_stages": [],
            "analyses": {},
            "decision_report_verification": {"status": "failed"},
            "_workflow_route_decisions": [],
        },
    )
    check = next(
        item
        for item in verification["checks"]
        if item["name"] == "terminal_decision_verification_passed"
    )
    assert verification["status"] == "failed"
    assert check["status"] == "fail"


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


def test_decision_graph_budget_is_deterministic_and_bounded():
    budget = {
        "max_llm_calls": 7,
        "max_tokens": 101,
        "max_cost_usd": 0.100001,
        "max_seconds": 13,
    }
    first = build_workflow_plan(
        workflow_type="deep",
        failed_test_ids=["t1"],
        decision_graph_aggregate_budget=budget,
    )
    second = build_workflow_plan(
        workflow_type="deep",
        failed_test_ids=["t1"],
        decision_graph_aggregate_budget=dict(reversed(list(budget.items()))),
    )
    assert first == second
    allocated = [
        stage["budget"] for stage in first["stages"]
        if stage["budget"]["max_llm_calls"] is not None
    ]
    assert sum(item["max_llm_calls"] for item in allocated) <= 7
    assert sum(item["max_tokens"] for item in allocated) <= 101
    assert sum(item["max_cost_usd"] for item in allocated) <= 0.100001
    assert sum(item["max_seconds"] for item in allocated) <= 13
    assert first["decision_graph_aggregate_budget"] == {
        "max_llm_calls": 7,
        "max_tokens": 101,
        "max_cost_usd": 0.100001,
        "max_seconds": 13,
    }


def test_decision_graph_budget_preserves_explicit_zero():
    plan = build_workflow_plan(
        workflow_type="offline",
        failed_test_ids=["t1"],
        decision_graph_aggregate_budget={
            "max_llm_calls": 0,
            "max_tokens": 0,
            "max_cost_usd": 0,
            "max_seconds": 0,
        },
    )
    assert plan["decision_graph_aggregate_budget"]["max_llm_calls"] == 0
    assert all(
        stage["budget"]["max_llm_calls"] == 0
        for stage in plan["stages"]
        if stage["budget"]["max_llm_calls"] is not None
    )

def test_log_intelligence_stage_is_flagged_and_hashed():
    enabled = build_workflow_plan(
        workflow_type="deep", failed_test_ids=["tc-1"], analyses={},
        log_intelligence_enabled=True,
    )
    disabled = build_workflow_plan(
        workflow_type="deep", failed_test_ids=["tc-1"], analyses={},
        log_intelligence_enabled=False,
    )
    log_on = next(item for item in enabled["stages"] if item["stage"] == "log_intelligence")
    log_off = next(item for item in disabled["stages"] if item["stage"] == "log_intelligence")
    assert log_on["planned"] is True
    assert log_off["planned"] is False
    assert enabled["plan_sha256"] != disabled["plan_sha256"]
def test_regression_watchman_stage_is_flagged_and_hashed():
    enabled = build_workflow_plan(
        workflow_type="deep", failed_test_ids=["tc-1"], analyses={},
        regression_watchman_enabled=True,
    )
    disabled = build_workflow_plan(
        workflow_type="deep", failed_test_ids=["tc-1"], analyses={},
        regression_watchman_enabled=False,
    )
    on = next(item for item in enabled["stages"] if item["stage"] == "regression_watchman")
    off = next(item for item in disabled["stages"] if item["stage"] == "regression_watchman")
    assert on["planned"] is True
    assert off["planned"] is False
    assert enabled["plan_sha256"] != disabled["plan_sha256"]