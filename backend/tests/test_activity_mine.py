"""``GET /api/v1/activity/mine`` against a scripted session (W21d ACT-02 §2.2).

Each statement the read issues is compiled for PostgreSQL and recorded, so the
tests assert the parts' real WHERE text (no OR across tables; B filters on
``event_type``; C and D join on ``project_id`` too), the order of the checks
(a non-member's QUERY ``project_id`` is refused before ANY read; zero
memberships issue no query at all), the merge (de-duplication keeps the
highest-priority involvement; keyset paging) and the cursor's binding to the
caller. The real Postgres behaviour is ``integration/test_activity_mine_postgres.py``.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.dialects import postgresql

from app.core import deps
from app.models.postgres import UserRole
from app.routers import activity as activity_router
from app.services.activity import mine
from app.services.activity.live_vocab import MINE_ERROR_SENTENCES
from app.services.activity.query import InvalidCursor


def _row(*, at: datetime, entity_type: str = "release", project_id=None, **extra) -> Any:
    base = dict(
        id=uuid.uuid4(), project_id=project_id or uuid.uuid4(), release_id=None,
        occurred_at=at, category="releases", event_type="release.updated",
        actor_type="user", actor_id=None, actor_name="Ana", actor_ref=None,
        entity_type=entity_type, entity_id=str(uuid.uuid4()), entity_label="R1",
        target_type=None, target_id=None, summary="Ana changed R1", context=None,
        diff=None, source_table=None, source_id=None, group_key=None,
    )
    base.update(extra)
    return SimpleNamespace(**base)


class _Result:
    def __init__(self, rows: list[tuple] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return list(self._rows)

    def scalar_one(self):
        return self._scalar


class _Session:
    """Answers each statement by the part it is; records the compiled SQL."""

    def __init__(self, parts: dict[str, list[tuple]] | None = None, total: int = 0, members=()):
        self.parts = parts or {}
        self.total = total
        self.members = list(members)
        self.sql: list[str] = []

    @staticmethod
    def kind(sql: str) -> str:
        if sql.lstrip().startswith("SELECT count(*)"):
            return "count"
        if "FROM project_members" in sql:
            return "members"
        if "JOIN managed_test_cases" in sql:
            return "C"
        if "JOIN defects" in sql:
            return "D"
        if "@>" in sql:
            return "B"
        return "A"

    async def execute(self, stmt):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        self.sql.append(sql)
        kind = self.kind(sql)
        if kind == "count":
            return _Result(scalar=self.total)
        if kind == "members":
            return _Result(rows=[(pid,) for pid in self.members])
        return _Result(rows=self.parts.get(kind, []))

    def of(self, kind: str) -> list[str]:
        return [s for s in self.sql if self.kind(s) == kind]


def _filters(**kw) -> mine.MyActivityFilters:
    base = dict(view=None, since=None, until=None, limit=None, cursor=None)
    base.update(kw)
    return activity_router.my_activity_filters(**base)


def _user(role=UserRole.QA_ENGINEER):
    return SimpleNamespace(id=uuid.uuid4(), role=role)


@pytest.fixture
def accessible(monkeypatch):
    """What ``get_accessible_project_ids`` answers (None = ADMIN); no Redis."""
    holder: dict[str, Any] = {"value": set()}

    async def _fake(db, user):
        return holder["value"]

    monkeypatch.setattr(deps, "get_accessible_project_ids", _fake)
    return holder


async def _call(session, user, filters, project_id=None) -> dict:
    return await activity_router.list_my_activity(
        filters=filters, project_id=project_id, db=session, current_user=user
    )


# ── The dependency: defaults and the 422 sentences ───────────────────────────


def test_defaults_are_all_seven_days_fifty():
    before = datetime.now(timezone.utc)
    filters = _filters()
    assert (filters.view, filters.limit, filters.cursor, filters.until) == ("all", 50, None, None)
    assert before - timedelta(days=7, seconds=5) <= filters.since <= before - timedelta(days=7) + timedelta(seconds=5)
    assert _filters(view="", cursor=" ").view == "all"


@pytest.mark.parametrize(
    ("kwargs", "sentence"),
    [
        ({"view": "nonsense"}, MINE_ERROR_SENTENCES["unknown_view"]),
        ({"since": "OLD"}, MINE_ERROR_SENTENCES["window_too_long"]),
        ({"since": "NOW", "until": "YESTERDAY"}, MINE_ERROR_SENTENCES["since_after_until"]),
    ],
)
def test_the_422_sentences(kwargs, sentence):
    now = datetime.now(timezone.utc)
    times = {"OLD": now - timedelta(days=91), "NOW": now, "YESTERDAY": now - timedelta(days=1)}
    resolved = {k: times.get(v, v) for k, v in kwargs.items()}
    with pytest.raises(HTTPException) as refused:
        _filters(**resolved)
    assert refused.value.status_code == 422
    assert refused.value.detail == sentence


@pytest.mark.parametrize("limit", [0, 101])
def test_an_out_of_range_limit_is_fastapis_own_422(limit):
    with pytest.raises(RequestValidationError) as refused:
        _filters(limit=limit)
    (error,) = refused.value.errors()
    assert error["loc"] == ("query", "limit")


def test_a_naive_since_is_read_as_utc_not_a_500():
    naive = (datetime.now(timezone.utc) - timedelta(days=2)).replace(tzinfo=None)
    assert _filters(since=naive).since.tzinfo is not None


# ── Scope first: refusals and the empty case issue no ledger read ───────────


async def test_a_non_members_query_project_id_is_403_before_any_read(accessible):
    accessible["value"] = {uuid.uuid4()}
    session = _Session()
    with pytest.raises(HTTPException) as refused:
        await _call(session, _user(), _filters(), project_id=uuid.uuid4())
    assert refused.value.status_code == 403
    assert refused.value.detail == "You do not have access to this project"
    assert session.sql == [], "nothing was read before the scope was decided"


async def test_zero_memberships_answer_empty_without_a_query(accessible):
    accessible["value"] = set()
    session = _Session()
    answer = await _call(session, _user(), _filters())
    assert session.sql == []
    assert (answer["items"], answer["total"], answer["projects"], answer["next_cursor"]) == ([], 0, 0, None)
    assert answer["view"] == "all" and answer["window"]["since"]


# ── The parts' SQL, per view ─────────────────────────────────────────────────


def _assert_no_or_across_tables(sql: str) -> None:
    for clause in re.findall(r"\(([^()]* OR [^()]*)\)", sql):
        assert re.fullmatch(
            r"mtc\.assignee_id = \S+ OR mtc\.author_id = \S+", clause.strip()
        ), f"an OR across tables: {clause}"


async def test_view_mine_reads_only_part_a_inside_the_scope(accessible):
    project = uuid.uuid4()
    accessible["value"] = {project}
    session = _Session(members=[project])
    user = _user()
    await _call(session, user, _filters(view="mine"))

    (part_a,) = session.of("A")
    assert "project_activity_events.actor_type = " in part_a
    assert "project_activity_events.actor_id = " in part_a
    assert "project_activity_events.project_id IN" in part_a
    assert "ORDER BY project_activity_events.occurred_at DESC, project_activity_events.id DESC" in part_a
    assert "LIMIT" in part_a and "JOIN" not in part_a
    assert session.of("B") == session.of("C") == session.of("D") == []
    assert len(session.of("count")) == 1


async def test_view_involving_reads_b_c_d_each_excluding_my_own_actions(accessible):
    project = uuid.uuid4()
    accessible["value"] = {project}
    session = _Session(members=[project])
    await _call(session, _user(), _filters(view="involving"))

    assert session.of("A") == []
    (b,) = session.of("B")
    assert "project_activity_events.event_type = " in b
    assert "CAST(project_activity_events.context AS JSONB)" in b and "@>" in b
    assert "project_activity_events.project_id IN" in b
    (c,) = session.of("C")
    assert "AND project_activity_events.project_id = mtc.project_id" in c
    assert "project_activity_events.entity_type IN" in c and "mtc.project_id IN" in c
    (d,) = session.of("D")
    assert "AND project_activity_events.project_id = d.project_id" in d
    assert "d.assignee_user_id = " in d and "d.project_id IN" in d
    for sql in (b, c, d):
        assert "project_activity_events.actor_id IS DISTINCT FROM" in sql
        _assert_no_or_across_tables(sql)


async def test_view_all_reads_four_parts_and_admin_unpinned_is_unscoped(accessible):
    accessible["value"] = None  # ADMIN
    member_of = uuid.uuid4()
    session = _Session(members=[member_of])
    answer = await _call(session, _user(UserRole.ADMIN), _filters())

    assert [len(session.of(k)) for k in ("A", "B", "C", "D", "count")] == [1, 1, 1, 1, 1]
    assert "project_id IN" not in session.of("A")[0], "ADMIN unpinned: every project"
    assert "project_activity_events.project_id IN" in session.of("B")[0], (
        "mentions are read only in the caller's OWN memberships, ADMIN included"
    )
    assert "IS DISTINCT FROM" not in session.of("C")[0], "'all' keeps my own rows"
    assert answer["projects"] is None


async def test_no_membership_of_my_own_skips_part_b(accessible):
    accessible["value"] = None
    session = _Session(members=[])
    await _call(session, _user(UserRole.ADMIN), _filters(view="involving"))
    assert session.of("B") == [] and len(session.of("C")) == 1


async def test_a_pinned_project_scopes_every_part(accessible):
    pinned, other = uuid.uuid4(), uuid.uuid4()
    accessible["value"] = {pinned, other}
    session = _Session(members=[pinned, other])
    answer = await _call(session, _user(), _filters(), project_id=pinned)
    assert answer["projects"] == 1
    count_sql = session.of("count")[0]
    assert "UNION" in count_sql
    for kind in ("A", "B", "C", "D"):
        assert "IN (__[POSTCOMPILE" in session.of(kind)[0]


# ── The merge: priority, ordering, paging, total ─────────────────────────────


async def test_one_row_in_several_parts_is_listed_once_with_the_highest_priority(accessible):
    project = uuid.uuid4()
    accessible["value"] = {project}
    user = _user()
    now = datetime.now(timezone.utc)
    mine_and_assigned = _row(at=now - timedelta(minutes=1), entity_type="test_case", project_id=project)
    mentioned_and_authored = _row(at=now - timedelta(minutes=2), entity_type="test_case", project_id=project)
    defect = _row(at=now - timedelta(minutes=3), entity_type="defect", project_id=project)
    session = _Session(
        parts={
            "A": [(mine_and_assigned,)],
            "B": [(mentioned_and_authored,)],
            "C": [(mine_and_assigned, user.id), (mentioned_and_authored, uuid.uuid4())],
            "D": [(defect,)],
        },
        total=3,
        members=[project],
    )
    answer = await _call(session, user, _filters())
    assert [(i["id"], i["involvement"]) for i in answer["items"]] == [
        (str(mine_and_assigned.id), "actor"),
        (str(mentioned_and_authored.id), "mentioned"),
        (str(defect.id), "assigned"),
    ]
    assert answer["total"] == 3 and answer["next_cursor"] is None
    item = answer["items"][0]
    assert "diff" not in item and item["entity"]["href"], "the feed's own item shape"


async def test_c_says_authored_when_i_wrote_the_case_and_someone_else_has_it(accessible):
    project = uuid.uuid4()
    accessible["value"] = {project}
    authored = _row(at=datetime.now(timezone.utc), entity_type="test_case", project_id=project)
    session = _Session(parts={"C": [(authored, uuid.uuid4())]}, total=1, members=[project])
    answer = await _call(session, _user(), _filters(view="involving"))
    assert [i["involvement"] for i in answer["items"]] == ["authored"]


async def test_more_rows_than_the_limit_give_a_cursor_bound_to_the_caller(accessible):
    project = uuid.uuid4()
    accessible["value"] = {project}
    user = _user()
    now = datetime.now(timezone.utc)
    rows = [_row(at=now - timedelta(minutes=n), project_id=project) for n in range(3)]
    session = _Session(parts={"A": [(r,) for r in rows]}, total=3, members=[project])
    answer = await _call(session, user, _filters(view="mine", limit=2))
    assert [i["id"] for i in answer["items"]] == [str(rows[0].id), str(rows[1].id)]
    assert mine.decode_user_cursor(answer["next_cursor"], user.id) == (rows[1].occurred_at, rows[1].id)

    # The next page carries the keyset bound inside every part.
    session.sql.clear()
    await _call(session, user, _filters(view="mine", limit=2, cursor=answer["next_cursor"]))
    assert "(project_activity_events.occurred_at, project_activity_events.id) <" in session.of("A")[0]


async def test_another_users_cursor_is_400(accessible):
    accessible["value"] = {uuid.uuid4()}
    theirs = mine.encode_user_cursor(datetime.now(timezone.utc), uuid.uuid4(), uuid.uuid4())
    session = _Session()
    with pytest.raises(HTTPException) as refused:
        await _call(session, _user(), _filters(cursor=theirs))
    assert refused.value.status_code == 400
    assert refused.value.detail == "Cursor was issued for a different user"
    assert session.of("A") == []


def test_a_malformed_cursor_is_named_so():
    with pytest.raises(InvalidCursor, match="Cursor is malformed"):
        mine.decode_user_cursor("not-a-cursor", uuid.uuid4())
