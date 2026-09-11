"""The flat canonical test-case list is paged, and its pages are stable.

Re-audit M7. ``GET /api/v1/canonical-test-cases`` returned every row the caller
could see -- measured at 1,237 for an unscoped admin -- with ``total`` equal to
the length of what it had just returned. Canonical rows are one per test per
project and are kept forever, so that list only ever grows.

These run the real service function on a real (SQLite) database, because what
matters is what the SQL returns: disjoint pages that cover every row, a
``total`` that counts every match, a page cut in SQL rather than in Python,
and a stable order when test names tie.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.models.postgres import CanonicalTestCase, Project  # noqa: E402
from app.services import test_suite_service as svc  # noqa: E402

pytestmark = pytest.mark.regression


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in (Project.__table__, CanonicalTestCase.__table__):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


async def _project(db, *, active: bool = True) -> uuid.UUID:
    pid = uuid.uuid4()
    db.add(Project(id=pid, name=f"p-{pid.hex[:6]}", slug=f"p-{pid.hex}", is_active=active))
    await db.flush()
    return pid


async def _cases(db, project_id: uuid.UUID, names: list[str]) -> list[uuid.UUID]:
    ids = []
    for i, name in enumerate(names):
        cid = uuid.uuid4()
        db.add(CanonicalTestCase(
            id=cid,
            project_id=project_id,
            test_suite_id=uuid.uuid4(),
            test_fingerprint=f"{project_id.hex[:8]}{i:04d}",
            test_name=name,
        ))
        ids.append(cid)
    await db.flush()
    return ids


@pytest.fixture
async def seeded(db):
    """Seven live cases across two projects -- three share a name -- plus a
    deleted project's three, which must never appear."""
    live_a = await _project(db)
    live_b = await _project(db)
    gone = await _project(db, active=False)
    live = await _cases(db, live_a, ["checkout", "checkout", "login", "search"])
    live += await _cases(db, live_b, ["checkout", "login", "zeta"])
    dead = await _cases(db, gone, ["checkout", "login", "search"])
    await db.commit()
    return {"live": set(live), "dead": set(dead)}


async def test_pages_are_disjoint_and_cover_every_live_row(db, seeded):
    seen: list[uuid.UUID] = []
    for page in (1, 2, 3):
        rows, total = await svc.list_canonical_test_cases(
            db, project_ids=None, page=page, size=3
        )
        assert total == 7, "total is not the count of every match"
        seen += [row.id for row in rows]
    assert len(seen) == len(set(seen)), "a row appeared on two pages"
    assert set(seen) == seeded["live"], "paging skipped rows, or leaked a deleted project's"


async def test_a_page_is_at_most_size_long(db, seeded):
    rows, total = await svc.list_canonical_test_cases(db, project_ids=None, page=1, size=2)
    assert len(rows) == 2
    assert total == 7


async def test_a_page_past_the_end_is_empty_but_still_counts(db, seeded):
    rows, total = await svc.list_canonical_test_cases(db, project_ids=None, page=9, size=3)
    assert rows == []
    assert total == 7


async def test_the_page_is_cut_in_sql_with_a_tie_break(db, seeded, monkeypatch):
    """Two things only the SQL shows.

    Slicing in Python after reading every row returns identical pages and
    totals, so only the statement can tell it apart. And ``test_name`` is not
    unique, so OFFSET needs a tie-break -- SQLite happens to return ties in
    insertion order, which would hide its absence here.
    """
    statements = []
    real_execute = db.execute

    async def _spy(statement, *args, **kwargs):
        statements.append(statement)
        return await real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", _spy)
    await svc.list_canonical_test_cases(db, project_ids=None, page=2, size=3)

    page_sql = str(statements[-1].compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT 3" in page_sql and "OFFSET 3" in page_sql, (
        "the page is not cut in SQL -- every row is still read and materialised"
    )
    order_by = page_sql.split("ORDER BY", 1)[1]
    assert "canonical_test_cases.test_name ASC, canonical_test_cases.id ASC" in order_by, order_by


async def test_unpaged_calls_still_get_everything(db, seeded):
    """The suite view selects across the whole suite, so it must not get one page."""
    rows, total = await svc.list_canonical_test_cases(db, project_ids=None)
    assert {row.id for row in rows} == seeded["live"]
    assert total == 7


async def test_an_empty_membership_still_fails_closed(db, seeded):
    rows, total = await svc.list_canonical_test_cases(db, project_ids=[], page=1, size=3)
    assert (rows, total) == ([], 0)
