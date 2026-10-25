"""W13 TCL-03: the evidence-gap list's two new kinds, ``flaky_active`` and ``no_owner``.

They page the library health's two lists with the SAME predicates (one rule,
``test_library_health_service``), a total beside every page, and their extra
fields (``owner_state`` / ``flaky_score`` / ``flaky_confidence``). The two old
kinds keep their statements (``tests/test_retired_cases_leave_counts.py`` pins
them, unedited).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.test_library_vocab import FLAKY_SCORE_FLOOR, RETIRED_STATES, RUNNABLE_STATES
from app.services import test_suite_service


class _Result:
    def __init__(self, *, scalar=None, rows=()):
        self._scalar, self._rows = scalar, list(rows)

    def scalar(self):
        return self._scalar

    def all(self):
        return list(self._rows)


def _case(**kw):
    base = dict(id=uuid.uuid4(), project_id=uuid.uuid4(), title="Pay", status="active", last_executed_at=None,
                assignee_id=None, author_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _sql(call) -> tuple[str, dict]:
    compiled = call.args[0].compile()
    return str(compiled), compiled.params


async def test_flaky_active_is_paged_with_its_total_and_score():
    project = uuid.uuid4()
    case = _case(project_id=project)
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        _Result(scalar=31), _Result(rows=[(case, 0.72, "high")]),
    ]))

    items, total = await test_suite_service.list_test_case_evidence_gaps(
        db, [project], kind="flaky_active", page=2, size=25,
    )

    assert total == 31
    assert items == [{
        "id": case.id, "project_id": project, "title": "Pay", "status": "active", "last_executed_at": None,
        "owner_state": None, "flaky_score": 0.72, "flaky_confidence": "high",
    }]
    assert db.execute.await_count == 2
    count_sql, _ = _sql(db.execute.await_args_list[0])
    page_sql, params = _sql(db.execute.await_args_list[1])
    for text in (count_sql, page_sql):
        assert "JOIN flaky_score ON flaky_score.project_id = managed_test_cases.project_id" in text
        assert "canonical_test_cases.managed_test_case_id = managed_test_cases.id" in text
        assert "flaky_score.confidence NOT IN" in text
        assert "managed_test_cases.project_id IN" in text
    assert "ORDER BY flaky_score.score DESC, managed_test_cases.id ASC" in page_sql
    assert FLAKY_SCORE_FLOOR in params.values()
    assert any(isinstance(v, (list, tuple)) and set(v) == set(RUNNABLE_STATES) for v in params.values())
    assert any(isinstance(v, (list, tuple)) and set(v) == {"none", "low"} for v in params.values())
    assert params.get("param_1") == 25 or 25 in params.values()      # LIMIT size
    assert 25 in params.values()                                     # OFFSET (page 2)


async def test_no_owner_is_paged_with_its_total_and_owner_state():
    project = uuid.uuid4()
    unassigned = _case(project_id=project, status="draft")
    deactivated = _case(project_id=project, author_id=uuid.uuid4())
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        _Result(scalar=2), _Result(rows=[(unassigned, None, None), (deactivated, None, None)]),
    ]))

    items, total = await test_suite_service.list_test_case_evidence_gaps(db, [project], kind="no_owner")

    assert total == 2
    assert [i["owner_state"] for i in items] == ["unassigned", "deactivated"]
    assert all(i["flaky_score"] is None and i["flaky_confidence"] is None for i in items)
    page_sql, params = _sql(db.execute.await_args_list[1])
    assert "LEFT OUTER JOIN users ON users.id = coalesce(managed_test_cases.assignee_id, " \
           "managed_test_cases.author_id)" in page_sql
    assert "coalesce(users.is_active, " in page_sql and "IS false" in page_sql
    assert "managed_test_cases.status NOT IN" in page_sql
    assert any(isinstance(v, (list, tuple)) and set(v) == set(RETIRED_STATES) for v in params.values())
    assert "ORDER BY managed_test_cases.created_at ASC, managed_test_cases.id ASC" in page_sql


@pytest.mark.parametrize("kind", ["flaky_active", "no_owner"])
async def test_a_member_of_no_project_reads_nothing(kind):
    db = SimpleNamespace(execute=AsyncMock())
    assert await test_suite_service.list_test_case_evidence_gaps(db, [], kind=kind) == ([], 0)
    db.execute.assert_not_awaited()


@pytest.mark.parametrize("kind", ["flaky_active", "no_owner"])
async def test_all_projects_reads_every_project_with_its_own_scores(kind):
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Result(scalar=0), _Result(rows=[])]))
    assert await test_suite_service.list_test_case_evidence_gaps(db, None, kind=kind) == ([], 0)
    page_sql, _ = _sql(db.execute.await_args_list[1])
    assert "managed_test_cases.project_id IN" not in page_sql


@pytest.mark.parametrize("kind", ["never_executed", "automation_vanished"])
async def test_the_old_kinds_never_touch_the_new_tables(kind):
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Result(scalar=0), _Result(rows=[])]))
    await test_suite_service.list_test_case_evidence_gaps(db, [uuid.uuid4()], kind=kind)
    assert db.execute.await_count == 2
    for call in db.execute.await_args_list:
        text, _ = _sql(call)
        assert "flaky_score" not in text and "users" not in text
        assert "LEFT OUTER JOIN canonical_test_cases" in text


async def test_an_unknown_kind_is_still_refused():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await test_suite_service.list_test_case_evidence_gaps(SimpleNamespace(), None, kind="bogus")
    assert exc.value.status_code == 422
