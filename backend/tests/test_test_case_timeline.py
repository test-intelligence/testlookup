"""W13 TCL-05: one timeline per test case (``GET /cases/{case_id}/timeline``).

1. Rows of three kinds at the SAME ``occurred_at`` come back by kind rank, then
   id descending, the same on every call.
2. The cursor round trip: paging by 3 yields exactly the one-page answer — no
   duplicate, no gap, the boundary at equal timestamps included.
3. A cursor of another case, a malformed one, and an unknown kind are 422.
4. ``kinds=comment`` is ONE statement; an automation kind adds the identity
   read; with no identity, ``automation_linked`` is false and no run is read.
5. Every entry has a title, and an ``actor_name`` that is a name or None.

1, 2 and 5 run the real SQL (the keyset row comparisons included) on an
in-memory SQLite database holding the case's own tables; 3 and 4 run over a
recording fake. Every time is built inside the test.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.test_library_vocab import TIMELINE_CURSOR_INVALID, TIMELINE_KIND_INVALID
from app.models.postgres import TestCaseComment as Comment
from app.models.postgres import TestCaseReview as Review
from app.models.postgres import TestCaseVersion as Version
from app.models.postgres import User
from app.routers import test_management_library as routes
from app.services import test_case_timeline_service as timeline


def _aware(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class _AwareSession:
    """The real SQLite session, statements recorded, times read back as UTC (PostgreSQL's timestamptz)."""

    def __init__(self, session):
        self.session, self.statements = session, []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        result = await self.session.execute(stmt, *args, **kwargs)
        rows = [SimpleNamespace(**{k: _aware(v) for k, v in row._mapping.items()}) for row in result.all()]
        return SimpleNamespace(all=lambda: rows, scalar=lambda: rows[0] if rows else None)


@pytest.fixture
async def sqlite():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    tables = [User.__table__, Version.__table__, Review.__table__, Comment.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(User.metadata.create_all, tables=tables)
        # PostgreSQL's index is partial (open reviews only); SQLite would make it total.
        await conn.exec_driver_sql("DROP INDEX uq_test_case_reviews_one_open_per_case")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _case(**kw):
    base = dict(id=uuid.uuid4(), project_id=uuid.uuid4(), ai_generated=False, test_fingerprint=None)
    base.update(kw)
    return SimpleNamespace(**base)


async def _seed(factory, case, now):
    """7 versions + 5 comments at ONE instant, 2 reviews (requested and decided) at it too, 3 older comments."""
    user = User(id=uuid.uuid4(), email="tl@example.invalid", username="tl-user", full_name="",
                hashed_password="x", role="TESTER", is_active=True)
    async with factory() as s:
        s.add(user)
        for n in range(7):
            s.add(Version(id=uuid.uuid4(), test_case_id=case.id, version=n + 1, title="T", status="draft",
                          change_type="updated", changed_by_id=user.id, created_at=now))
        for n in range(5):
            s.add(Comment(id=uuid.uuid4(), test_case_id=case.id, content=f"c{n}", created_at=now))
        for n in range(3):
            s.add(Comment(id=uuid.uuid4(), test_case_id=case.id, content=f"old{n}",
                          comment_type="question", created_at=now - timedelta(minutes=n + 1)))
        for n in range(2):
            s.add(Review(id=uuid.uuid4(), test_case_id=case.id, status="approved", created_at=now,
                         reviewed_at=now, reviewer_id=user.id, requested_by_id=user.id, human_notes="ok"))
        await s.commit()
    return user


_CASE_KINDS = ["version", "review_requested", "review_decided", "comment"]


async def _read(factory, case, *, limit, cursor=None, kinds=_CASE_KINDS):
    async with factory() as s:
        return await timeline.case_timeline(_AwareSession(s), case, kinds=kinds, cursor=cursor, limit=limit)


def _key(entry):
    return entry["kind"], entry["id"]


async def test_equal_instants_order_by_kind_rank_then_id_descending(sqlite):
    case, now = _case(), datetime.now(timezone.utc).replace(microsecond=0)
    await _seed(sqlite, case, now)

    first = await _read(sqlite, case, limit=100)
    again = await _read(sqlite, case, limit=100)

    items = first["items"]
    assert [_key(e) for e in items] == [_key(e) for e in again["items"]]
    at_now = [e for e in items if e["occurred_at"] == now]
    kinds = [e["kind"] for e in at_now]
    assert kinds == ["version"] * 7 + ["review_requested"] * 2 + ["review_decided"] * 2 + ["comment"] * 5
    for kind in ("version", "comment"):
        ids = [uuid.UUID(e["id"]) for e in at_now if e["kind"] == kind]
        assert ids == sorted(ids, reverse=True)
    assert [e["detail"] for e in items[-3:]] == ["old0", "old1", "old2"]      # older ones after, newest first
    assert first["next_cursor"] is None and first["kinds"] == _CASE_KINDS


async def test_paging_by_three_is_exactly_the_one_page_answer(sqlite):
    case, now = _case(), datetime.now(timezone.utc).replace(microsecond=0)
    await _seed(sqlite, case, now)
    whole = [_key(e) for e in (await _read(sqlite, case, limit=100))["items"]]

    paged, cursor, pages = [], None, 0
    while True:
        page = await _read(sqlite, case, limit=3, cursor=cursor)
        paged += [_key(e) for e in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert pages < 20
    assert paged == whole                       # no duplicate, no gap
    assert len(set(paged)) == len(paged) == 19
    assert pages == 7                           # 19 entries / 3 (the last page holds one)


async def test_every_entry_has_a_title_and_a_name_or_none(sqlite):
    case, now = _case(), datetime.now(timezone.utc).replace(microsecond=0)
    await _seed(sqlite, case, now)
    items = (await _read(sqlite, case, limit=100))["items"]
    assert all(e["title"] for e in items)
    assert all(e["actor_name"] is None or e["actor_name"].strip() for e in items)
    assert {e["actor_name"] for e in items if e["kind"] == "version"} == {"tl-user"}   # full name "" -> username
    assert {e["actor_name"] for e in items if e["kind"] == "comment"} == {None}         # nobody recorded
    assert {e["title"] for e in items if e["kind"] == "comment"} == {"Comment", "Question"}
    assert {e["title"] for e in items if e["kind"] == "review_decided"} == {"Approved"}


# ── the cursor and the kinds ─────────────────────────────────────────────────


def test_a_cursor_of_another_case_or_a_malformed_one_is_refused():
    case_id, other = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    good = timeline.encode_cursor(now, "comment", str(uuid.uuid4()), case_id)
    assert timeline.decode_cursor(good, case_id)[0] == now
    for bad in (timeline.encode_cursor(now, "comment", str(uuid.uuid4()), other), "not-a-cursor", "e30",
                timeline.encode_cursor(now, "bogus", str(uuid.uuid4()), case_id),
                timeline.encode_cursor(now, "review_decided", str(uuid.uuid4()), case_id)):   # no ":decided"
        with pytest.raises(HTTPException) as exc:
            timeline.decode_cursor(bad, case_id)
        assert (exc.value.status_code, exc.value.detail) == (422, TIMELINE_CURSOR_INVALID)


async def test_an_unknown_kind_is_refused_before_any_statement():
    db = SimpleNamespace(execute=AsyncMock())
    case = _case()
    with pytest.raises(HTTPException) as exc:
        await routes.get_test_case_timeline(case_id=case.id, kinds=["comment", "bogus"], cursor=None, limit=50,
                                            db=db, current_user=SimpleNamespace(), case=case)
    assert (exc.value.status_code, exc.value.detail) == (422, TIMELINE_KIND_INVALID)
    db.execute.assert_not_awaited()


class _Rows:
    def __init__(self, rows=(), scalar=None):
        self._rows, self._scalar = list(rows), scalar

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar


async def test_one_statement_per_kind_and_the_identity_only_for_automation_kinds():
    case = _case()
    db = SimpleNamespace(execute=AsyncMock(return_value=_Rows()))
    body = await timeline.case_timeline(db, case, kinds=["comment"], cursor=None, limit=50)
    assert db.execute.await_count == 1 and body["automation_linked"] is False
    assert "LIMIT" in str(db.execute.await_args_list[0].args[0].compile())

    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Rows(scalar=None)]))     # no identity
    body = await timeline.case_timeline(db, case, kinds=["automated_execution", "defect"], cursor=None, limit=50)
    assert db.execute.await_count == 1 and body["automation_linked"] is False and body["items"] == []

    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Rows(scalar="fp-1"), _Rows(), _Rows()]))
    body = await timeline.case_timeline(db, case, kinds=["automated_execution", "defect"], cursor=None, limit=50)
    assert db.execute.await_count == 3 and body["automation_linked"] is True
    runs_sql = str(db.execute.await_args_list[1].args[0].compile())
    assert "test_runs.project_id = " in runs_sql and "test_cases.test_fingerprint = " in runs_sql
    assert "test_case_history" not in runs_sql


async def test_all_kinds_read_eight_sources_and_the_identity():
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Rows(scalar="fp-1")] + [_Rows()] * 8))
    body = await timeline.case_timeline(db, _case(), kinds=[], cursor=None, limit=50)
    assert db.execute.await_count == 9 and len(body["kinds"]) == 8 and body["next_cursor"] is None
