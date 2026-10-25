"""W13 TCL-01 / TCL-06 on real PostgreSQL: the case list's new filters, the requirement terms, the export.

Through the real app over HTTP, against real rows:

* ``owner`` is the assignee, else the author: a case I authored but assigned
  to Priya is NOT mine; one I authored that nobody is assigned to IS;
* ``automation=not_automated`` counts ``is_automated IS NULL`` as not automated (checked
  through the service: the list's response model refuses a NULL, a pre-W13 500);
* ``status`` repeats (an ``IN`` over three states);
* ``requirement=[req-pay-07]`` finds the case linked to ``REQ-PAY-07`` and never
  another project's; the search box finds it by ``pay-07``;
* the Excel export with the same QUERY lists the same ids (column 19);
* ``owner=<a member of another project only>`` -> 422 with the filter's sentence.
"""
from __future__ import annotations

import io
import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CASES = "/api/v1/test-management/cases"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


def _redis_client():
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        import redis.asyncio as redis_asyncio

        return redis_asyncio.Redis.from_url(url, decode_responses=True)
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest_asyncio.fixture
async def world():
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import ManagedTestCase, Project, ProjectMember, TestCaseRequirementLink, User, UserRole

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    redis = _redis_client()
    patch = pytest.MonkeyPatch()
    app.dependency_overrides[get_db] = _get_db
    patch.setattr("app.db.redis_client.get_redis", lambda: redis)
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    tag = uuid.uuid4().hex[:10]
    project_a, project_b = uuid.uuid4(), uuid.uuid4()
    me, priya, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ids = {key: uuid.uuid4() for key in ("mine", "assigned_priya", "null_auto", "assigned_me", "linked", "retired", "foreign")}
    try:
        async with sessions.begin() as db:
            db.add_all([
                Project(id=project_a, name=f"w13-tcl01-a-{tag}", slug=f"w13-tcl01-a-{tag}", is_active=True),
                Project(id=project_b, name=f"w13-tcl01-b-{tag}", slug=f"w13-tcl01-b-{tag}", is_active=True),
            ])
            for uid, label, role in ((me, "me", UserRole.QA_ENGINEER), (priya, "priya", UserRole.TESTER),
                                     (outsider, "out", UserRole.TESTER)):
                db.add(User(id=uid, email=f"w13-tcl01-{label}-{tag}@example.invalid", username=f"w13_tcl01_{label}_{tag}",
                            full_name=f"TCL-01 {label}", hashed_password="!unusable", role=role.value, is_active=True))
            await db.flush()
            db.add_all([
                ProjectMember(project_id=project_a, user_id=me, role=UserRole.QA_ENGINEER.value),
                ProjectMember(project_id=project_a, user_id=priya, role=UserRole.TESTER.value),
                ProjectMember(project_id=project_b, user_id=outsider, role=UserRole.TESTER.value),
            ])

            def case(key, project, *, author, status, assignee=None, automated=False, title=None, priority="high"):
                return ManagedTestCase(
                    id=ids[key], project_id=project, title=title or f"Case {key} {tag}", status=status, version=1,
                    author_id=author, assignee_id=assignee, is_automated=automated, priority=priority,
                    severity="major", test_type="functional", automation_status="not_automated", ai_generated=False,
                )

            db.add_all([
                case("mine", project_a, author=me, status="draft", priority="critical"),
                case("assigned_priya", project_a, author=me, assignee=priya, status="review_requested", automated=True),
                case("null_auto", project_a, author=priya, status="active"),
                case("assigned_me", project_a, author=priya, assignee=me, status="approved"),
                case("linked", project_a, author=priya, status="draft", title=f"Refund within a day {tag}"),
                case("retired", project_a, author=me, status="deprecated"),
                case("foreign", project_b, author=outsider, status="draft", title=f"Refund elsewhere {tag}"),
            ])
            await db.flush()
            for key, project in (("linked", project_a), ("foreign", project_b)):
                db.add(TestCaseRequirementLink(
                    project_id=project, test_case_id=ids[key], requirement_key="[req-pay-07]",
                    requirement_key_norm="REQ-PAY-07", link_type="verifies", created_by_id=None,
                ))

        def _jwt(uid):
            return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(client=client, sessions=sessions, project_a=project_a, ids=ids, me=me,
                                  priya=priya, outsider=outsider, headers=_jwt(me))
        finally:
            await client.aclose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        patch.undo()
        async with sessions.begin() as db:
            await db.execute(text("DELETE FROM projects WHERE id IN (:a, :b)"), {"a": project_a, "b": project_b})
            await db.execute(text("DELETE FROM users WHERE id IN (:a, :b, :c)"), {"a": me, "b": priya, "c": outsider})
        await engine.dispose()
        await redis.aclose()
        await app_postgres.dispose_engine_for_loop()


async def _ids(world, query: str) -> set[uuid.UUID]:
    resp = await world.client.get(f"{CASES}?project_id={world.project_a}&size=50&{query}", headers=world.headers)
    assert resp.status_code == 200, resp.text
    return {uuid.UUID(item["id"]) for item in resp.json()["items"]}


def _keys(world, *keys) -> set[uuid.UUID]:
    return {world.ids[key] for key in keys}


async def test_owner_is_the_assignee_else_the_author(world):
    assert await _ids(world, "owner=me") == _keys(world, "mine", "assigned_me")
    assert await _ids(world, f"owner={world.priya}") == _keys(world, "assigned_priya", "null_auto", "linked")
    refused = await world.client.get(f"{CASES}?project_id={world.project_a}&owner={world.outsider}",
                                     headers=world.headers)
    assert refused.status_code == 422
    assert refused.json()["detail"] == "The owner filter names someone who is not an active member of this project."


async def test_automation_counts_null_as_not_automated_and_statuses_repeat(world):
    from app.services.test_management_service import CaseListFilters, list_managed_test_cases

    assert await _ids(world, "automation=not_automated") == _keys(world, "mine", "null_auto", "assigned_me", "linked")
    assert await _ids(world, "automation=automated") == _keys(world, "assigned_priya")
    # A NULL is_automated (the column allows it; the ORM writes False) is "not
    # automated". Read through the service: the list's response model refuses
    # a NULL ``is_automated`` (a 500 that predates W13, reported as a follow-up).
    async with world.sessions.begin() as db:
        await db.execute(text("UPDATE managed_test_cases SET is_automated = NULL WHERE id = :id"),
                         {"id": world.ids["null_auto"]})
    async with world.sessions() as db:
        rows, total, _pages = await list_managed_test_cases(
            db, world.project_a, page=1, size=50, filters=CaseListFilters(automation="not_automated"),
        )
        automated, _t, _p = await list_managed_test_cases(
            db, world.project_a, page=1, size=50, filters=CaseListFilters(automation="automated"),
        )
    assert {row.id for row in rows} == _keys(world, "mine", "null_auto", "assigned_me", "linked") and total == 4
    assert {row.id for row in automated} == _keys(world, "assigned_priya")
    three = "status=draft&status=review_requested&status=approved"
    assert await _ids(world, three) == _keys(world, "mine", "assigned_priya", "assigned_me", "linked")
    # A status list drops the default retired exclusion, as one status does today.
    assert await _ids(world, "status=deprecated&status=draft") == _keys(world, "mine", "linked", "retired")


async def test_the_requirement_filter_and_the_search_find_the_linked_case_in_this_project_only(world):
    assert await _ids(world, "requirement=%5Breq-pay-07%5D") == _keys(world, "linked")
    assert await _ids(world, "search=pay-07") == _keys(world, "linked")
    resp = await world.client.get(
        f"{CASES}?project_id={world.project_a}&include_automation=true&owner=me", headers=world.headers,
    )
    assert resp.json()["automation_rows_excluded"] is True


async def test_p0_finds_the_critical_rows_and_an_unknown_priority_is_refused(world):
    assert await _ids(world, "priority=P0") == _keys(world, "mine")
    assert await _ids(world, "priority=p0&priority=P1") == _keys(world, "mine", "assigned_priya", "null_auto",
                                                                   "assigned_me", "linked")
    for path in (CASES, f"{CASES}/export/excel"):
        resp = await world.client.get(f"{path}?project_id={world.project_a}&priority=urgent", headers=world.headers)
        assert resp.status_code == 422, path
        assert resp.json()["detail"] == "priority must be critical, high, medium or low (or P0 to P3)."
    openpyxl = pytest.importorskip("openpyxl")
    resp = await world.client.get(f"{CASES}/export/excel?project_id={world.project_a}&priority=P0",
                                  headers=world.headers)
    sheet = openpyxl.load_workbook(io.BytesIO(resp.content))["Test Cases"]
    assert [sheet.cell(row=r, column=19).value for r in range(2, sheet.max_row + 1)] == [str(world.ids["mine"])]


async def test_the_export_with_the_same_query_lists_the_same_ids(world):
    openpyxl = pytest.importorskip("openpyxl")
    query = "status=draft&status=review_requested&status=approved&owner=me"
    listed = await _ids(world, query)
    resp = await world.client.get(f"{CASES}/export/excel?project_id={world.project_a}&{query}", headers=world.headers)
    assert resp.status_code == 200, resp.text
    sheet = openpyxl.load_workbook(io.BytesIO(resp.content))["Test Cases"]
    assert [sheet.cell(row=1, column=c).value for c in (19, 20, 21)] == ["ID", "Suite", "Description"]
    exported = {uuid.UUID(sheet.cell(row=r, column=19).value) for r in range(2, sheet.max_row + 1)}
    assert exported == listed == _keys(world, "mine", "assigned_me")

    resp = await world.client.get(f"{CASES}/export/excel?project_id={world.project_a}&search=pay-07",
                                  headers=world.headers)
    sheet = openpyxl.load_workbook(io.BytesIO(resp.content))["Test Cases"]
    assert [sheet.cell(row=r, column=19).value for r in range(2, sheet.max_row + 1)] == [str(world.ids["linked"])]
