"""Read-path tests for the activity ledger (epic ACT).

Cursor pagination and the tenant boundary. The pagination tests deliberately
INSERT between page fetches, because that is the condition the existing audit
dashboard gets wrong and the reason this feed pages by keyset at all: the table
is appended to while it is being read.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.postgres import Base, Project, ProjectActivityEvent, User
from app.services.activity import query as q

_TABLES = [Project.__table__, User.__table__, ProjectActivityEvent.__table__]


class _AwareDateTime(SQLITE_DATETIME):
    def result_processor(self, dialect, coltype):  # noqa: D102
        inner = super().result_processor(dialect, coltype)

        def process(value):
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def db(session_factory):
    async with session_factory() as session:
        yield session


@pytest.fixture
async def projects(db):
    a = Project(id=uuid.uuid4(), name="Auth Service", slug="auth-service")
    b = Project(id=uuid.uuid4(), name="Payment Service", slug="payment-service")
    db.add_all([a, b])
    await db.commit()
    return a, b


async def _add(db, project_id, *, minutes_ago: int, summary: str, **kw):
    row = ProjectActivityEvent(
        id=uuid.uuid4(),
        project_id=project_id,
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        recorded_at=datetime.now(timezone.utc),
        category=kw.pop("category", "runs"),
        event_type=kw.pop("event_type", "run.completed"),
        schema_version=1,
        actor_type=kw.pop("actor_type", "system"),
        actor_name=kw.pop("actor_name", "ingestion"),
        entity_type=kw.pop("entity_type", "run"),
        entity_id=kw.pop("entity_id", str(uuid.uuid4())),
        entity_label=kw.pop("entity_label", "Build 1"),
        summary=summary,
        **kw,
    )
    db.add(row)
    await db.commit()
    return row


# ── Cursor ───────────────────────────────────────────────────────────────────


def test_cursor_round_trips():
    pid = uuid.uuid4()
    ts = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    rid = uuid.uuid4()
    decoded_ts, decoded_id = q.decode_cursor(q.encode_cursor(ts, rid, pid), pid)
    assert decoded_ts == ts
    assert decoded_id == rid


def test_a_cursor_from_another_project_is_rejected():
    """Not a security control — require_project_access is. This catches the
    confusing case where a client reuses a cursor across projects and would
    otherwise get a page from the wrong keyset position."""
    a, b = uuid.uuid4(), uuid.uuid4()
    cursor = q.encode_cursor(datetime.now(timezone.utc), uuid.uuid4(), a)
    with pytest.raises(q.InvalidCursor):
        q.decode_cursor(cursor, b)


@pytest.mark.parametrize("bad", ["", "!!!!", "YWJj", "x" * 50])
def test_a_malformed_cursor_is_rejected_not_ignored(bad):
    with pytest.raises(q.InvalidCursor):
        q.decode_cursor(bad, uuid.uuid4())


# ── Pagination ───────────────────────────────────────────────────────────────


async def test_pages_do_not_overlap_or_skip_when_rows_arrive_between_fetches(
    db, projects
):
    """The reason this feed uses a keyset cursor at all.

    The ledger is appended to WHILE it is read — every ingest writes to it. With
    LIMIT/OFFSET, a row inserted between page 1 and page 2 shifts everything
    down by one, so page 2 repeats the last row of page 1. This test inserts
    exactly that row and asserts the pages stay disjoint and complete.
    """
    a, _ = projects
    for i in range(6):
        await _add(db, a.id, minutes_ago=100 - i, summary=f"event {i}")

    first = await q.list_events(db, a.id, q.ActivityFilters(limit=3))
    ids_1 = [e["id"] for e in first["items"]]
    assert len(ids_1) == 3
    assert first["next_cursor"]

    # A newer row lands between the two requests — the exact race.
    await _add(db, a.id, minutes_ago=0, summary="arrived mid-read")

    second = await q.list_events(
        db, a.id, q.ActivityFilters(limit=3, cursor=first["next_cursor"])
    )
    ids_2 = [e["id"] for e in second["items"]]

    assert not set(ids_1) & set(ids_2), "pages overlapped — a row was shown twice"
    # The newest-first ordering means the interloper belongs on page 1, which
    # the reader has already passed; what matters is that nothing was SKIPPED
    # from the original six.
    seen = set(ids_1) | set(ids_2)
    assert len(seen) == 6


async def test_next_cursor_is_null_on_the_last_page(db, projects):
    a, _ = projects
    for i in range(2):
        await _add(db, a.id, minutes_ago=i, summary=f"e{i}")

    page = await q.list_events(db, a.id, q.ActivityFilters(limit=50))
    assert len(page["items"]) == 2
    assert page["next_cursor"] is None


async def test_feed_is_newest_first(db, projects):
    a, _ = projects
    await _add(db, a.id, minutes_ago=60, summary="older")
    await _add(db, a.id, minutes_ago=1, summary="newer")

    page = await q.list_events(db, a.id, q.ActivityFilters())
    assert [e["summary"] for e in page["items"]] == ["newer", "older"]


# ── Tenant isolation ─────────────────────────────────────────────────────────


async def test_a_query_never_returns_another_projects_rows(db, projects):
    a, b = projects
    await _add(db, a.id, minutes_ago=1, summary="belongs to auth")
    await _add(db, b.id, minutes_ago=1, summary="belongs to payment")

    page = await q.list_events(db, a.id, q.ActivityFilters())
    assert [e["summary"] for e in page["items"]] == ["belongs to auth"]


async def test_get_event_refuses_an_id_from_another_project(db, projects):
    """Defence in depth behind require_project_access: even handed a valid
    event id, the query is scoped, so a guard regression cannot leak one row."""
    a, b = projects
    row = await _add(db, b.id, minutes_ago=1, summary="payment only")

    assert await q.get_event(db, a.id, row.id) is None
    assert await q.get_event(db, b.id, row.id) is not None


# ── Filters ──────────────────────────────────────────────────────────────────


async def test_category_filter(db, projects):
    a, _ = projects
    await _add(db, a.id, minutes_ago=1, summary="a run", category="runs")
    await _add(
        db, a.id, minutes_ago=2, summary="a config change",
        category="configuration", event_type="policy.updated", entity_type="policy",
    )

    page = await q.list_events(
        db, a.id, q.ActivityFilters(categories=("configuration",))
    )
    assert [e["summary"] for e in page["items"]] == ["a config change"]


async def test_actor_type_filter_uses_the_stored_vocabulary(db, projects):
    """`"quarantined"` vs stored `QUARANTINED` matched nothing for months
    (#735). These columns store lowercase, and the filter must too."""
    a, _ = projects
    await _add(db, a.id, minutes_ago=1, summary="by a person", actor_type="user")
    await _add(db, a.id, minutes_ago=2, summary="by the system", actor_type="system")

    page = await q.list_events(db, a.id, q.ActivityFilters(actor_type="user"))
    assert [e["summary"] for e in page["items"]] == ["by a person"]

    page_upper = await q.list_events(db, a.id, q.ActivityFilters(actor_type="USER"))
    assert page_upper["items"] == [], "filter matched a vocabulary that is not stored"


async def test_free_text_search_matches_the_summary(db, projects):
    a, _ = projects
    await _add(db, a.id, minutes_ago=1, summary="Attribution rule catch-all enabled")
    await _add(db, a.id, minutes_ago=2, summary="Build 4312 completed")

    page = await q.list_events(db, a.id, q.ActivityFilters(q="attribution"))
    assert len(page["items"]) == 1


async def test_a_search_below_the_minimum_is_ignored_not_applied(db, projects):
    a, _ = projects
    await _add(db, a.id, minutes_ago=1, summary="Build 4312 completed")

    page = await q.list_events(db, a.id, q.ActivityFilters(q="ab"))
    assert len(page["items"]) == 1, "a 2-char query should be ignored, not filtered on"


async def test_limit_is_capped(db, projects):
    a, _ = projects
    page = await q.list_events(db, a.id, q.ActivityFilters(limit=10_000))
    assert page["items"] == []  # no rows, but the call must not explode


# ── ledger_started_at ────────────────────────────────────────────────────────


async def test_ledger_started_at_ignores_every_filter(db, projects):
    """A release or category filter leaking in here would make a populated
    project look brand new — the same shape as the bug that showed the
    first-run wizard on a project full of runs."""
    a, _ = projects
    await _add(db, a.id, minutes_ago=10_000, summary="the very first event")
    await _add(db, a.id, minutes_ago=1, summary="a recent one")

    narrow = await q.list_events(
        db,
        a.id,
        q.ActivityFilters(
            categories=("configuration",),
            since=datetime.now(timezone.utc) - timedelta(minutes=5),
        ),
    )
    assert narrow["items"] == []
    assert narrow["ledger_started_at"] is not None, (
        "the empty state cannot tell the reader when the ledger starts"
    )


async def test_ledger_started_at_is_null_for_a_project_with_no_activity(db, projects):
    a, _ = projects
    page = await q.list_events(db, a.id, q.ActivityFilters())
    assert page["ledger_started_at"] is None


# ── Serialisation ────────────────────────────────────────────────────────────


async def test_feed_rows_never_carry_the_diff(db, projects):
    """The diff can hold a full before/after payload. Shipping it on every row
    of every page would be the largest thing in the response and is only ever
    read one event at a time."""
    a, _ = projects
    row = await _add(db, a.id, minutes_ago=1, summary="changed")
    row.diff = {"before": {"x": 1}, "after": {"x": 2}, "changed_fields": ["x"]}
    await db.commit()

    page = await q.list_events(db, a.id, q.ActivityFilters())
    assert "diff" not in page["items"][0]
    assert page["items"][0]["has_diff"] is True

    detail = await q.get_event(db, a.id, row.id)
    assert detail["diff"]["changed_fields"] == ["x"]


async def test_entity_href_is_resolved_server_side(db, projects):
    a, _ = projects
    await _add(db, a.id, minutes_ago=1, summary="run", entity_id="abc")

    page = await q.list_events(db, a.id, q.ActivityFilters())
    assert page["items"][0]["entity"]["href"] == "/runs/abc"


# ── Export ───────────────────────────────────────────────────────────────────


async def test_export_reports_truncation_rather_than_silently_cutting(
    db, projects, monkeypatch
):
    a, _ = projects
    monkeypatch.setattr(q, "EXPORT_MAX_ROWS", 2)
    for i in range(3):
        await _add(db, a.id, minutes_ago=i, summary=f"e{i}")

    rows, truncated = await q.iter_export_rows(db, a.id, q.ActivityFilters())
    assert len(rows) == 2
    assert truncated is True


async def test_csv_export_has_a_header_and_one_line_per_row(db, projects):
    a, _ = projects
    await _add(db, a.id, minutes_ago=1, summary="one thing happened")

    rows, _ = await q.iter_export_rows(db, a.id, q.ActivityFilters())
    csv_text = q.rows_to_csv(rows)
    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    assert lines[0].startswith("occurred_at,")
    assert len(lines) == 2
    assert "one thing happened" in lines[1]


async def test_ndjson_export_is_one_json_object_per_line(db, projects):
    import json

    a, _ = projects
    await _add(db, a.id, minutes_ago=1, summary="one thing happened")

    rows, _ = await q.iter_export_rows(db, a.id, q.ActivityFilters())
    lines = [ln for ln in q.rows_to_ndjson(rows).splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["summary"] == "one thing happened"
