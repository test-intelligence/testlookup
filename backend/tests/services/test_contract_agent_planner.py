from app.services.agent_planner import build_workflow_plan


def _stage(plan, name):
    return next(item for item in plan["stages"] if item["stage"] == name)


def test_contract_stage_is_planned_only_when_enabled_and_failures_exist():
    enabled = build_workflow_plan(
        workflow_type="deep",
        failed_test_ids=["tc-1"],
        analyses={"tc-1": {"failure_category": "PRODUCT_BUG"}},
        contract_validation_enabled=True,
    )
    disabled = build_workflow_plan(
        workflow_type="deep",
        failed_test_ids=["tc-1"],
        analyses={"tc-1": {"failure_category": "PRODUCT_BUG"}},
        contract_validation_enabled=False,
    )
    assert _stage(enabled, "contract_validation")["planned"] is True
    assert _stage(disabled, "contract_validation")["planned"] is False


def test_contract_stage_is_skipped_for_all_green_runs_even_when_enabled():
    plan = build_workflow_plan(
        workflow_type="deep",
        failed_test_ids=[],
        analyses={},
        contract_validation_enabled=True,
    )
    stage = _stage(plan, "contract_validation")
    assert stage["planned"] is False
    assert "no failed tests" in stage["rationale"]


def test_contract_flag_is_hashed_into_plan():
    off = build_workflow_plan(
        workflow_type="deep", failed_test_ids=["tc-1"], analyses={},
        contract_validation_enabled=False,
    )
    on = build_workflow_plan(
        workflow_type="deep", failed_test_ids=["tc-1"], analyses={},
        contract_validation_enabled=True,
    )
    assert off["plan_sha256"] != on["plan_sha256"]