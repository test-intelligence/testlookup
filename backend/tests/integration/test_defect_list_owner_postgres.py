"""DEF-02 on real PostgreSQL: every defect row says who owns it.

The defect's own team wins (``owner_source: defect``); a row whose team is
blank or the promotion's ``Unknown`` gets the team ITS project's ownership
rules resolve for its test (``rule``, ``component_map``, ``test_label``),
else both stay null. A rule of project A is never applied to project B's
rows, the resolution costs at most two statements per page (none when every
row has its own team), and ``assignee_name`` is the assignee's name only for a
person who may own work (not a service, synthetic or inactive account).

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (migrated to head) and ``REDIS_URL``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text, update

from tests.integration.defect_w06_world import StatementCounter, defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime.now(timezone.utc)


@pytest.fixture
async def world():
    from app.models.postgres import LaunchStatus, Project, ServiceOwnershipRule, TestRun

    async with defect_world() as w:
        async with w.sessions.begin() as db:
            await db.execute(
                update(Project).where(Project.id == w.P).values(component_owner_map={"Cart*": {"team": "Cart Team"}})
            )
            db.add(ServiceOwnershipRule(project_id=w.P, match_type="suite_name", match_pattern="checkout*",
                                        service_name="checkout", team_name="Payments Team", priority=10))
            # Project Q's rule matches P's "billing" suite: it must never answer for P.
            db.add(ServiceOwnershipRule(project_id=w.Q, match_type="suite_name", match_pattern="billing*",
                                        service_name="billing", team_name="Q Billing Team", priority=10))
            run_q = uuid.uuid4()
            db.add(TestRun(id=run_q, project_id=w.Q, build_number=f"w06a-q-{w.tag}", jenkins_job="w06a",
                           status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=1, failed_tests=1))
            await db.flush()
            # One OPEN defect per test case (``ix_defects_test_case_open_unique``).
            checkout = await w.case(db, suite_name="checkout-api")
            checkout_own = await w.case(db, suite_name="checkout-api")
            cart = await w.case(db, suite_name="web", class_name="CartService")
            labelled = await w.case(db, suite_name="web", class_name="Search", owner="search-squad")
            billing = await w.case(db, suite_name="billing-x", class_name="Invoice")
            q_billing = await w.case(db, run_id=run_q, suite_name="billing-x", class_name="Invoice")

            human = await w.user(db, full_name="Priya Nair")
            service = await w.user(db, full_name="CI Bot", is_service_account=True)
            synthetic = await w.user(db, full_name="QA lead (synthetic)", is_synthetic=True)
            inactive = await w.user(db, full_name="Gone Person", is_active=False)
            w.users = {"human": human, "service": service, "synthetic": synthetic, "inactive": inactive}

            def created(i):
                return NOW - timedelta(minutes=i)

            w.rows = {
                "own_team": await w.defect(db, owner_team="Platform", test_case_id=checkout_own, created_at=created(1)),
                "rule": await w.defect(db, owner_team="Unknown", test_case_id=checkout, created_at=created(2)),
                "component_map": await w.defect(db, owner_team="  ", test_case_id=cart, created_at=created(3)),
                "test_label": await w.defect(db, test_case_id=labelled, created_at=created(4)),
                "nobody": await w.defect(db, test_case_id=billing, created_at=created(5)),
                "no_test": await w.defect(db, created_at=created(6)),
                "assigned_human": await w.defect(db, owner_team="Platform", assignee_user_id=human, created_at=created(7)),
                "assigned_service": await w.defect(db, owner_team="Platform", assignee_user_id=service, created_at=created(8)),
                "assigned_synthetic": await w.defect(db, owner_team="Platform", assignee_user_id=synthetic, created_at=created(9)),
                "assigned_inactive": await w.defect(db, owner_team="Platform", assignee_user_id=inactive, created_at=created(10)),
                "q_billing": await w.defect(db, project_id=w.Q, test_case_id=q_billing, created_at=created(11)),
            }
        w.name_of = {str(v): k for k, v in w.rows.items()}
        yield w


async def _rows(w, project_id=None, allowed=None, size=100):
    from app.services import analytics_service

    async with w.sessions() as db:
        listed = await analytics_service.list_defects(
            db, str(project_id) if project_id else None, None, 1, size, allowed_project_ids=allowed,
        )
    return {w.name_of[str(item["id"])]: item for item in listed["items"] if str(item["id"]) in w.name_of}


async def test_each_row_names_its_owner_and_where_it_came_from(world):
    rows = await _rows(world, world.P)
    got = {name: (row["owner_team"], row["owner_source"]) for name, row in rows.items()}
    assert got["own_team"] == ("Platform", "defect")
    assert got["rule"] == ("Payments Team", "rule")
    assert got["component_map"] == ("Cart Team", "component_map")
    assert got["test_label"] == ("search-squad", "test_label")
    assert got["nobody"] == (None, None)
    assert got["no_test"] == (None, None)
    # The rule-matching helper columns never reach the wire.
    assert not any(key.startswith("own_") for row in rows.values() for key in row)


async def test_a_rule_of_one_project_never_answers_for_another(world):
    """All-projects scope over P and Q: Q's billing rule matches P's billing suite by name."""
    rows = await _rows(world, allowed={world.P, world.Q})
    assert (rows["nobody"]["owner_team"], rows["nobody"]["owner_source"]) == (None, None)
    assert (rows["q_billing"]["owner_team"], rows["q_billing"]["owner_source"]) == ("Q Billing Team", "rule")


async def test_the_assignee_name_is_only_a_person_who_may_own_work(world):
    rows = await _rows(world, world.P)
    assert rows["assigned_human"]["assignee_name"] == "Priya Nair"
    assert rows["assigned_human"]["assignee_user_id"] == world.users["human"]
    for name in ("assigned_service", "assigned_synthetic", "assigned_inactive"):
        assert rows[name]["assignee_name"] is None, name
        assert rows[name]["assignee_user_id"] is not None, "the stored id is still reported"
    assert rows["rule"]["assignee_name"] is None and rows["rule"]["assignee_user_id"] is None


async def test_the_new_keys_are_on_every_row(world):
    rows = await _rows(world, world.P)
    for row in rows.values():
        assert row["project_id"] == world.P
        assert row["priority"] in ("P0", "P1", "P2", "P3")
        assert row["approval_status"] == "approved"
        assert row["jira_filing_status"] is None and row["jira_filing_reason"] is None


async def test_the_jira_filing_outcome_is_read_from_the_policy_evaluation(world):
    async with world.sessions.begin() as db:
        await db.execute(text(
            "UPDATE defects SET policy_evaluation = CAST(:pe AS json) WHERE id = :id"
        ), {"pe": '{"jira_filing": {"status": "not_filed", "reason": "offline_mode", "at": "x"}}',
            "id": world.rows["rule"]})
    rows = await _rows(world, world.P)
    assert (rows["rule"]["jira_filing_status"], rows["rule"]["jira_filing_reason"]) == ("not_filed", "offline_mode")


async def _count(world, project_id, size):
    from app.services import analytics_service

    async with world.sessions() as db:
        await db.execute(text("SELECT 1"))
        with StatementCounter(world.engine) as counter:
            await analytics_service.list_defects(db, str(project_id), None, 1, size)
    return len(counter.statements)


async def test_two_extra_statements_per_page_at_most_and_none_when_every_row_has_a_team(world):
    async with world.sessions.begin() as db:
        many = await world.project(db)
        run = uuid.uuid4()
        from app.models.postgres import LaunchStatus, TestRun

        db.add(TestRun(id=run, project_id=many, build_number=f"w06a-m-{world.tag}", jenkins_job="w06a",
                       status=LaunchStatus.FAILED, ingestion_source="unknown", total_tests=20, failed_tests=20))
        await db.flush()
        for i in range(20):
            await world.defect(db, project_id=many, owner_team="Unknown", test_case_id=await world.case(db, run_id=run))
        owned = await world.project(db)
        for i in range(3):
            await world.defect(db, project_id=owned, owner_team=f"Team {i}")
    assert await _count(world, many, 20) == await _count(world, many, 1) == 4  # rows + count + rules + maps
    assert await _count(world, owned, 20) == 2  # rows + count


async def test_defect_row_is_one_row_of_this_project_only(world):
    from app.services import analytics_service

    async with world.sessions() as db:
        row = await analytics_service.defect_row(db, world.P, world.rows["rule"])
        assert row["id"] == world.rows["rule"] and row["owner_team"] == "Payments Team"
        assert await analytics_service.defect_row(db, world.Q, world.rows["rule"]) is None
        assert await analytics_service.defect_row(db, world.P, uuid.uuid4()) is None
        assert await analytics_service.defect_row(db, world.P, "not-a-uuid") is None
