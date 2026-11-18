"""D-0021 (GPT-6): a QA lead's reassignment of a failure by hand left no entry in the project's Activity log.

``reassign_failure`` changed ``test_cases.assigned_to_user_id`` and logged a structlog line only; the ledger had no
event for it. It now records ``failure.reassigned`` (owner decision Q4: this one event) on a real change, in the
caller's transaction: ``Failure <test> reassigned from <A> to <B>``, on the run. A same-owner submit records nothing,
and the agent path (which records ``agent.action_executed`` itself) opts out with ``ledger=False``.

Drives the real ``reassign_failure`` and the real feed (``activity.query.list_events``) against the SQLite world of
``test_subject_erasure_service.py``. The Postgres twin (the route, trigram search) is
``tests/integration/test_activity_failure_reassign_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.postgres import Base, TestCase, User, UserRole
from app.services.activity.query import ActivityFilters, list_events
from app.services.failed_test_reassignment_service import ReassignmentError, reassign_failure
from tests.test_subject_erasure_service import _user
from tests.test_subject_erasure_service import maker as _sqlite_maker

pytestmark = pytest.mark.regression

maker = pytest.fixture(name="maker")(_sqlite_maker.__wrapped__)  # type: ignore[attr-defined]

TEST_NAME = "synthetic_total_includes_delivery"


@pytest.fixture
async def world(maker: Any, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    assert {"test_runs", "project_members", "project_activity_events"} <= maker.created
    if "test_cases" not in maker.created:
        # Its ``duration_ms DESC NULLS LAST`` index is Postgres-only; the table itself is all this test needs.
        cases = Base.metadata.tables["test_cases"]
        monkeypatch.setattr(cases, "indexes", set())
        async with maker.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[cases])
    now = datetime.now(timezone.utc)
    w = SimpleNamespace(
        P=uuid.uuid4(), Q=uuid.uuid4(), run=uuid.uuid4(), case=uuid.uuid4(),
        lead=_user(UserRole.QA_LEAD, "zqxwlead"), a=_user(UserRole.QA_ENGINEER, "zqxwanna"),
        b=_user(UserRole.QA_ENGINEER, "zqxwbart"), viewer=_user(UserRole.VIEWER, "zqxwview"), now=now,
    )
    t = Base.metadata.tables
    async with maker.session() as db:
        db.add_all([w.lead, w.a, w.b, w.viewer])
        await db.flush()
        for project, name in ((w.P, "zqxw p"), (w.Q, "zqxw q")):
            await db.execute(t["projects"].insert().values(id=project, name=name, slug=f"zqxw-{project.hex[:8]}"))
        for user, role in ((w.lead, "QA_LEAD"), (w.a, "QA_ENGINEER"), (w.b, "QA_ENGINEER"), (w.viewer, "VIEWER")):
            await db.execute(t["project_members"].insert().values(id=uuid.uuid4(), project_id=w.P, user_id=user.id, role=role))
        await db.execute(t["test_runs"].insert().values(
            id=w.run, project_id=w.P, build_number="zqxw-1", status="FAILED", created_at=now - timedelta(hours=1)))
        await db.execute(t["test_cases"].insert().values(
            id=w.case, test_run_id=w.run, test_name=TEST_NAME, test_fingerprint=f"fp-{w.case.hex[:12]}", status="FAILED",
            assigned_to_user_id=w.a.id))
        # The feed's positive control in P, and another project's row.
        for project in (w.P, w.Q):
            await db.execute(t["project_activity_events"].insert().values(
                id=uuid.uuid4(), project_id=project, occurred_at=now - timedelta(minutes=30), category="analysis",
                event_type="review.rejected", actor_type="user", actor_id=w.lead.id, actor_name="Zqxwlead",
                entity_type="run", entity_id=str(w.run), summary="Zqxwlead rejected the AI report for run x",
            ))
        await db.commit()
    return w


async def _move(maker: Any, w: SimpleNamespace, to: User) -> None:
    async with maker.session() as db:
        lead = await db.get(User, w.lead.id)
        await reassign_failure(db, w.case, to.id, lead)
        await db.commit()


async def _feed(maker: Any, project: uuid.UUID, **kw: Any) -> list[dict]:
    async with maker.session() as db:
        page = await list_events(db, project, ActivityFilters(since=datetime.now(timezone.utc) - timedelta(days=7), **kw))
    return page["items"]


def _reassigned(items: list[dict]) -> list[dict]:
    return [item for item in items if item["event_type"] == "failure.reassigned"]


async def test_a_reassignment_by_hand_is_in_the_activity_log(maker, world):
    w = world
    await _move(maker, w, w.b)
    rows = _reassigned(await _feed(maker, w.P))
    assert len(rows) == 1
    row = rows[0]
    assert row["actor"]["id"] == str(w.lead.id)
    assert row["entity"]["type"] == "run" and row["entity"]["href"] == f"/runs/{w.run}"
    assert TEST_NAME in row["summary"] and "from Zqxwanna" in row["summary"] and "to Zqxwbart" in row["summary"]
    # The control is still there, and the move happened.
    assert any(item["event_type"] == "review.rejected" for item in await _feed(maker, w.P))
    async with maker.session() as db:
        assert (await db.get(TestCase, w.case)).assigned_to_user_id == w.b.id


async def test_the_searches_find_it_and_another_project_does_not(maker, world):
    w = world
    await _move(maker, w, w.b)
    for q in ("reassigned", "assigned", TEST_NAME):
        assert len(_reassigned(await _feed(maker, w.P, q=q))) == 1, q
    other = await _feed(maker, w.Q)
    assert _reassigned(other) == [] and all(TEST_NAME not in item["summary"] for item in other)


async def test_a_same_owner_submit_and_a_refused_move_record_nothing(maker, world):
    w = world
    await _move(maker, w, w.b)
    await _move(maker, w, w.b)  # already Bart's: nothing changes, nothing is recorded
    with pytest.raises(ReassignmentError):
        await _move(maker, w, w.viewer)  # not eligible: 422, rolled back
    assert len(_reassigned(await _feed(maker, w.P))) == 1


async def test_two_real_moves_are_two_events(maker, world):
    w = world
    await _move(maker, w, w.b)
    await _move(maker, w, w.a)
    assert len(_reassigned(await _feed(maker, w.P))) == 2


async def test_the_agent_path_records_nothing_here(maker, world):
    """``agent_action_ledger_service`` records ``agent.action_executed`` for an agent's move."""
    w = world
    async with maker.session() as db:
        lead = await db.get(User, w.lead.id)
        await reassign_failure(db, w.case, w.b.id, lead, ledger=False)
        await db.commit()
    assert _reassigned(await _feed(maker, w.P)) == []
