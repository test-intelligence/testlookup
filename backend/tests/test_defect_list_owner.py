"""DEF-02 unit: ``analytics_service._resolve_owner_teams`` (the Owner column), on a recording session.

The defect's own team wins; ``Unknown`` (what promotion stores for nobody) and
blanks do not count as a team; the rest get ITS project's ownership-rule
answer with the source family named; the helper columns are popped; at most
two statements for the page, none when nothing needs resolving. The
PostgreSQL half is ``tests/integration/test_defect_list_owner_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import analytics_service as svc

A, B = uuid.uuid4(), uuid.uuid4()


def _rule(project_id, pattern, team, match_type="suite_name"):
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, match_type=match_type, match_pattern=pattern,
        service_name="svc", team_name=team, team_contact=None,
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, rules=(), maps=()):
        self.rules = list(rules)
        self.maps = list(maps)
        self.statements: list = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        tables = {t.name for t in stmt.get_final_froms()}
        if tables == {"service_ownership_rules"}:
            return _Result(self.rules)
        if tables == {"projects"}:
            return _Result(self.maps)
        raise AssertionError(f"unexpected statement over {tables}")


class _NoDB:
    async def execute(self, *args, **kwargs):
        raise AssertionError("no statement expected")


def _row(project_id=A, owner_team=None, suite="checkout-api", test_case=True, **own):
    return {
        "id": uuid.uuid4(), "project_id": project_id, "owner_team": owner_team, "suite_name": suite,
        "own_test_case": uuid.uuid4() if test_case else None,
        "own_component": own.get("component"), "own_package": own.get("package"), "own_path": own.get("path"),
        "own_label": own.get("label"), "own_owner": own.get("owner"),
    }


@pytest.mark.asyncio
async def test_own_teams_need_no_statement():
    items = [_row(owner_team="Platform"), _row(owner_team="  Payments  ")]
    await svc._resolve_owner_teams(_NoDB(), items)
    assert [(i["owner_team"], i["owner_source"]) for i in items] == [("Platform", "defect"), ("Payments", "defect")]
    assert not any(key.startswith("own_") for item in items for key in item)


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", ["Unknown", "unknown", "", "   ", None])
async def test_unknown_and_blank_are_not_a_team(stored):
    items = [_row(owner_team=stored)]
    session = _Session(rules=[_rule(A, "checkout*", "Payments Team")])
    await svc._resolve_owner_teams(session, items)
    assert (items[0]["owner_team"], items[0]["owner_source"]) == ("Payments Team", "rule")


@pytest.mark.asyncio
async def test_each_source_family():
    items = [
        _row(suite="checkout-api"),
        _row(suite="web", component="CartService"),
        _row(suite="web", component="Search", owner="search-squad"),
        _row(suite="web", component="Search"),
    ]
    session = _Session(
        rules=[_rule(A, "checkout*", "Payments Team")],
        maps=[(A, {"Cart*": {"team": "Cart Team"}})],
    )
    await svc._resolve_owner_teams(session, items)
    assert [(i["owner_team"], i["owner_source"]) for i in items] == [
        ("Payments Team", "rule"), ("Cart Team", "component_map"), ("search-squad", "test_label"), (None, None),
    ]
    assert len(session.statements) == 2


@pytest.mark.asyncio
async def test_a_rule_of_project_a_never_answers_for_project_b():
    items = [_row(project_id=B, suite="checkout-api"), _row(project_id=A, suite="checkout-api")]
    session = _Session(rules=[_rule(A, "checkout*", "A Team")])
    await svc._resolve_owner_teams(session, items)
    assert (items[0]["owner_team"], items[0]["owner_source"]) == (None, None)
    assert (items[1]["owner_team"], items[1]["owner_source"]) == ("A Team", "rule")


@pytest.mark.asyncio
async def test_a_page_costs_two_statements_whatever_its_size():
    for size in (1, 20):
        session = _Session(rules=[_rule(A, "checkout*", "A Team")])
        await svc._resolve_owner_teams(session, [_row() for _ in range(size)])
        assert len(session.statements) == 2


@pytest.mark.asyncio
async def test_a_row_without_a_test_case_is_not_resolved():
    items = [_row(test_case=False)]
    await svc._resolve_owner_teams(_NoDB(), items)
    assert (items[0]["owner_team"], items[0]["owner_source"]) == (None, None)


@pytest.mark.asyncio
async def test_a_hand_built_row_without_the_new_keys_still_works():
    """Fixtures built before W06 carry none of the helper columns."""
    items = [{"id": "def-1", "jira_ticket_id": "QA-1"}]
    await svc._resolve_owner_teams(_NoDB(), items)
    assert items == [{"id": "def-1", "jira_ticket_id": "QA-1", "owner_team": None, "owner_source": None}]
