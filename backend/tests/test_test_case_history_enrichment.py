"""W28 TCM-04 / TCM-05: the case history's data and the plan items' versions.

``case_history`` answers every version (newest first, at most 100) with the
name of whoever saved it, its source (stored; an AI-generated case's first
version reads ``ai``; anything else older than 0222 reads ``None``, never a
guess), masked parameters, and the plan executions recorded against THAT
version. Two statements at most. ``list_plan_items`` answers each item's
``executed_case_version`` beside the case's current ``case_version`` (one
outer join).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.postgres import ManagedTestCase, TestCaseVersion as VersionRow, TestPlanItem as PlanItemRow
from app.services.test_case_editing_service import HISTORY_LIMIT, case_history

PROJECT = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000001")
WHEN = datetime(2026, 10, 18, 9, 30, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


def make_case(*, ai_generated=False, version=4) -> ManagedTestCase:
    return ManagedTestCase(
        id=uuid.uuid4(), project_id=PROJECT, title="Pay by card", status="active", version=version,
        ai_generated=ai_generated,
    )


def version_row(case, number, *, source=None, parameters=None, assignee_id=None, change_type="updated"):
    return VersionRow(
        id=uuid.uuid4(), test_case_id=case.id, version=number, title=f"Pay by card v{number}",
        status="draft", change_type=change_type, changed_by_id=uuid.uuid4(), source=source,
        parameters=parameters, assignee_id=assignee_id, created_at=WHEN, tags=["smoke"],
        steps=[{"step_number": 1, "action": f"Step of v{number}", "expected_result": None}],
    )


def scripted_db(version_rows, execution_rows=()):
    return SimpleNamespace(execute=AsyncMock(side_effect=[_Rows(version_rows), _Rows(execution_rows)]))


@pytest.mark.asyncio
async def test_names_full_name_then_username_and_none_for_a_deleted_user():
    case = make_case()
    v3, v2, v1 = version_row(case, 3), version_row(case, 2), version_row(case, 1)
    db = scripted_db([(v3, "Priya Shah", "priya"), (v2, "   ", "qa.engineer"), (v1, None, None)])
    history = await case_history(db, case)
    assert [h.version for h in history] == [3, 2, 1]
    assert [h.changed_by_name for h in history] == ["Priya Shah", "qa.engineer", None]


@pytest.mark.asyncio
async def test_source_is_stored_else_ai_for_an_ai_case_s_first_version_else_none():
    ai_case = make_case(ai_generated=True)
    rows = [
        (version_row(ai_case, 3, source="api"), "Key", "ci"),
        (version_row(ai_case, 2), "Priya Shah", "priya"),            # before 0222: not recorded
        (version_row(ai_case, 1), "Priya Shah", "priya"),            # the derivable AI first version
    ]
    history = await case_history(scripted_db(rows), ai_case)
    assert [h.source for h in history] == ["api", None, "ai"]

    person_case = make_case(ai_generated=False)
    rows = [(version_row(person_case, 2, source="person"), "P", "p"), (version_row(person_case, 1), "P", "p")]
    history = await case_history(scripted_db(rows), person_case)
    assert [h.source for h in history] == ["person", None]


@pytest.mark.asyncio
async def test_a_stored_source_wins_over_the_ai_fallback():
    ai_case = make_case(ai_generated=True)
    history = await case_history(scripted_db([(version_row(ai_case, 1, source="person"), "P", "p")]), ai_case)
    assert history[0].source == "person"


@pytest.mark.asyncio
async def test_masked_parameters_never_leave_the_server_with_their_value():
    case = make_case()
    params = [
        {"name": "password", "value": "hunter2", "mode": None, "masked": True},
        # Not a hex-looking value: the dump carries random uuid4 ids, and "abc" turned up inside one ~2 % of runs.
        {"name": "token", "value": "tok-secret-zz", "mode": "hidden", "masked": False},
        {"name": "user", "value": "alice", "mode": None, "masked": False},
    ]
    history = await case_history(scripted_db([(version_row(case, 1, parameters=params), "P", "p")]), case)
    dumped = [p.model_dump() for p in history[0].parameters]
    assert dumped == [
        {"name": "password", "value": None, "mode": None, "masked": True},
        {"name": "token", "value": None, "mode": "hidden", "masked": True},
        {"name": "user", "value": "alice", "mode": None, "masked": False},
    ]
    assert "hunter2" not in history[0].model_dump_json() and "tok-secret-zz" not in history[0].model_dump_json()
    # The ORM row itself is untouched (the history only reads).
    assert params[0]["value"] == "hunter2"


@pytest.mark.asyncio
async def test_assignee_and_the_existing_fields_come_through():
    case = make_case()
    owner = uuid.uuid4()
    row = version_row(case, 2, assignee_id=owner, change_type="restored")
    history = await case_history(scripted_db([(row, "P", "p")]), case)
    item = history[0]
    assert (item.assignee_id, item.change_type, item.title, item.tags) == (owner, "restored", "Pay by card v2", ["smoke"])
    assert item.steps[0].action == "Step of v2"


@pytest.mark.asyncio
async def test_executions_are_attached_to_their_version_only():
    case = make_case()
    plan_a, plan_b = uuid.uuid4(), uuid.uuid4()
    item_a, item_b, item_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [(version_row(case, n), "P", "p") for n in (3, 2, 1)]
    executions = [
        (1, item_a, "passed", WHEN, plan_a, "Release 4.2"),
        (3, item_b, "failed", WHEN, plan_b, "Smoke"),
        (1, item_c, "blocked", None, plan_b, "Smoke"),
        (7, uuid.uuid4(), "passed", WHEN, plan_a, "Release 4.2"),  # a version beyond the list: ignored
    ]
    history = await case_history(scripted_db(rows, executions), case)
    by_version = {h.version: [(e.plan_name, e.execution_status, e.item_id) for e in h.executions] for h in history}
    assert by_version == {
        3: [("Smoke", "failed", item_b)],
        2: [],
        1: [("Release 4.2", "passed", item_a), ("Smoke", "blocked", item_c)],
    }
    assert history[2].executions[0].plan_id == plan_a
    assert history[2].executions[0].executed_at == WHEN


@pytest.mark.asyncio
async def test_two_statements_at_most_and_one_when_there_is_no_version():
    case = make_case()
    db = scripted_db([(version_row(case, 1), "P", "p")])
    await case_history(db, case)
    assert db.execute.await_count == 2
    versions_sql = str(db.execute.await_args_list[0].args[0].compile())
    assert "LEFT OUTER JOIN users ON users.id = test_case_versions.changed_by_id" in versions_sql
    assert "ORDER BY test_case_versions.version DESC" in versions_sql
    assert db.execute.await_args_list[0].args[0].compile().params["param_1"] == HISTORY_LIMIT == 100
    executions_sql = str(db.execute.await_args_list[1].args[0].compile())
    assert "test_plan_items.executed_case_version IS NOT NULL" in executions_sql
    assert "JOIN test_plans ON test_plans.id = test_plan_items.plan_id" in executions_sql
    assert "test_plans.project_id = " in executions_sql  # scoped to the case's project
    assert "LIMIT" in executions_sql

    empty = SimpleNamespace(execute=AsyncMock(return_value=_Rows([])))
    assert await case_history(empty, case) == []
    assert empty.execute.await_count == 1


# ═══════════════════════ list_plan_items (TCM-05) ═══════════════════════


@pytest.mark.asyncio
async def test_list_plan_items_fills_case_version_beside_the_executed_version():
    from app.routers.test_management_plans import list_plan_items

    plan_id = uuid.uuid4()
    ran_old = PlanItemRow(
        id=uuid.uuid4(), plan_id=plan_id, test_case_id=uuid.uuid4(), order_index=0,
        execution_status="passed", executed_case_version=1, created_at=WHEN,
    )
    not_run = PlanItemRow(
        id=uuid.uuid4(), plan_id=plan_id, test_case_id=uuid.uuid4(), order_index=1,
        execution_status="not_run", executed_case_version=None, created_at=WHEN,
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(id=plan_id)),
        execute=AsyncMock(return_value=_Rows([(ran_old, 2), (not_run, None)])),
    )

    items = await list_plan_items(plan_id=plan_id, db=db, current_user=SimpleNamespace(), _plan=None)

    assert [(i.executed_case_version, i.case_version) for i in items] == [(1, 2), (None, None)]
    sql = str(db.execute.await_args.args[0].compile())
    assert "LEFT OUTER JOIN managed_test_cases ON managed_test_cases.id = test_plan_items.test_case_id" in sql
    assert "ORDER BY test_plan_items.order_index" in sql
    assert db.execute.await_count == 1
