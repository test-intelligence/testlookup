"""HON-07: the Cases search matches what its placeholder promises.

The box said "Search by ID, title, tags, owner, or requirement" while the
authored-case predicate was ``title ILIKE`` alone. It now matches the title,
the description, any tag and the case id by prefix (with or without the
``TC-`` the page prints), and the placeholder says "title, tags or ID".

Behavioural, on a real aiosqlite session, through both list paths the
``/test-management/cases`` route uses.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.models.postgres import ManagedTestCase, Project  # noqa: E402
from app.services import test_management_service as svc  # noqa: E402

pytestmark = pytest.mark.regression


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in (Project.__table__, ManagedTestCase.__table__):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


async def _seed(db) -> tuple[uuid.UUID, dict[str, ManagedTestCase]]:
    pid = uuid.uuid4()
    db.add(Project(id=pid, name=f"p-{pid.hex[:6]}", slug=f"p-{pid.hex}", is_active=True))
    other = uuid.uuid4()
    db.add(Project(id=other, name=f"o-{other.hex[:6]}", slug=f"o-{other.hex}", is_active=True))
    cases = {
        "tagged": ManagedTestCase(project_id=pid, title="Card is charged once", tags=["payments-critical", "smoke"]),
        "described": ManagedTestCase(project_id=pid, title="Refund", description="Covers the payments-critical refund path"),
        "plain": ManagedTestCase(project_id=pid, title="Login works", tags=["auth"]),
        # Another project's case carrying the same tag never leaks in.
        "foreign": ManagedTestCase(project_id=other, title="Foreign", tags=["payments-critical"]),
    }
    db.add_all(cases.values())
    await db.flush()
    return pid, cases


async def _managed_titles(db, pid, search: str) -> set[str]:
    items, _total, _pages = await svc.list_managed_test_cases(db, pid, page=1, size=50, search=search)
    return {item.title for item in items}


async def _combined_ids(db, pid, search: str) -> set[uuid.UUID]:
    # test_type keeps the union to the authored half (the automation half
    # reads per-run tables this fixture does not create).
    identities, _total, _pages = await svc.list_combined_test_case_identities(
        db, pid, include_archived=False, test_type="functional", search=search,
        suite_name=None, page=1, size=50,
    )
    return {entity_id for _source, entity_id in identities}


async def test_a_tag_value_finds_the_case_carrying_it(db):
    pid, cases = await _seed(db)

    assert await _managed_titles(db, pid, "payments-critical") == {"Card is charged once", "Refund"}
    assert await _combined_ids(db, pid, "payments-critical") == {cases["tagged"].id, cases["described"].id}
    assert await _managed_titles(db, pid, "SMOKE") == {"Card is charged once"}


async def test_the_id_matches_by_prefix_with_or_without_the_tc_label(db):
    pid, cases = await _seed(db)
    case_id = cases["plain"].id
    short = case_id.hex[:6]

    assert await _managed_titles(db, pid, short) == {"Login works"}
    # The page prints `TC-` + the first six characters, upper-cased.
    assert await _managed_titles(db, pid, f"TC-{short.upper()}") == {"Login works"}
    assert await _combined_ids(db, pid, f"tc-{short}") == {case_id}


async def test_the_title_still_matches_and_a_miss_is_empty(db):
    pid, _cases = await _seed(db)

    assert await _managed_titles(db, pid, "login") == {"Login works"}
    assert await _managed_titles(db, pid, "no-such-thing") == set()
