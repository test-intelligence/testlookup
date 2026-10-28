"""``GET /api/v1/activity/mine`` on a real Postgres (W21d ACT-02 §2.2).

Two users, three projects: A (both), B (user 1 only), C (user 2 only — user 1
was a member, wrote a row there, was mentioned and assigned there, then was
removed). User 1 never sees a row of C; a mention of user 1 in A shows
``mentioned``, a case user 1 is assigned in A ``assigned``, a case user 1
wrote ``authored``, a defect assigned in B ``assigned``, user 1's own edit of
their own case ``actor`` once; ``total`` is the distinct count; three pages
reproduce the unpaged order; ADMIN sees involvement in a project it is not a
member of; another user's cursor is 400; ``EXPLAIN`` of part A names
``ix_pae_actor_time`` and of part C ``ix_mtc_assignee`` / ``ix_mtc_author``.

Rows are seeded through the real write path (``service.record``) with their
own ``occurred_at``. The fixture awaits ``dispose_engine_for_loop()`` before
building its engine and after disposing it, and closes the app's Redis client
(the membership cache) so the next module never meets "Event loop is closed".
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (at head, ``0238``); skips otherwise.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


@pytest.fixture
async def world():
    from httpx import ASGITransport, AsyncClient

    from app.core import deps
    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.db.redis_client import close_redis
    from app.main import app
    from app.models.postgres import (
        Defect, ManagedTestCase, Project, ProjectActivityEvent, ProjectMember, User, UserRole,
    )

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

    patch = pytest.MonkeyPatch()
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _get_db
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    tag = "t" + uuid.uuid4().hex[:9]  # starts with a letter: never read as a phone number
    A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    u1, u2, admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ids = SimpleNamespace(
        A=A, B=B, C=C, u1=u1, u2=u2, admin=admin,
        case_a_assigned=uuid.uuid4(), case_a_authored=uuid.uuid4(),
        case_c=uuid.uuid4(), case_c_admin=uuid.uuid4(), defect_b=uuid.uuid4(),
    )
    async with sessions.begin() as db:
        for pid, label in ((A, "a"), (B, "b"), (C, "c")):
            db.add(Project(id=pid, name=f"Mine {label} {tag}", slug=f"mine-{label}-{tag}", is_active=True))
        for uid, label, role in ((u1, "one", UserRole.QA_ENGINEER), (u2, "two", UserRole.QA_ENGINEER),
                                 (admin, "admin", UserRole.ADMIN)):
            db.add(User(id=uid, email=f"mine-{label}-{tag}@example.com", username=f"mine_{label}_{tag}",
                        full_name=f"Mine {label}", hashed_password="!unusable", role=role.value))
        await db.flush()
        for pid, uid in ((A, u1), (A, u2), (B, u1), (C, u2), (C, u1)):
            db.add(ProjectMember(project_id=pid, user_id=uid, role=UserRole.QA_ENGINEER.value))
        db.add(ManagedTestCase(id=ids.case_a_assigned, project_id=A, title=f"Login {tag}",
                               assignee_id=u1, author_id=u2))
        db.add(ManagedTestCase(id=ids.case_a_authored, project_id=A, title=f"Logout {tag}",
                               assignee_id=u2, author_id=u1))
        db.add(ManagedTestCase(id=ids.case_c, project_id=C, title=f"Refund {tag}", assignee_id=u1, author_id=u2))
        db.add(ManagedTestCase(id=ids.case_c_admin, project_id=C, title=f"Audit {tag}",
                               assignee_id=admin, author_id=u2))
        db.add(Defect(id=ids.defect_b, project_id=B, title=f"Crash {tag}", resolution_status="OPEN",
                      approval_status="approved", assignee_user_id=u1))
    for uid in (u1, u2, admin):
        await deps.invalidate_membership_cache(uid)

    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        async with sessions() as db:
            users = {uid: await db.get(User, uid) for uid in (u1, u2, admin)}
        yield SimpleNamespace(
            sessions=sessions, engine=engine, http=http, tag=tag, ids=ids, users=users,
            auth={uid: {"Authorization": f"Bearer {create_access_token(str(uid))}"} for uid in (u1, u2, admin)},
        )
    finally:
        await http.aclose()
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous
        patch.undo()
        async with sessions.begin() as db:
            for model in (ProjectActivityEvent, ManagedTestCase, Defect, ProjectMember):
                await db.execute(delete(model).where(model.project_id.in_([A, B, C])))
            await db.execute(delete(Project).where(Project.id.in_([A, B, C])))
            await db.execute(text("DELETE FROM access_audit_logs WHERE actor_user_id = ANY(:ids)"),
                             {"ids": [u1, u2, admin]})
            await db.execute(delete(User).where(User.id.in_([u1, u2, admin])))
        for uid in (u1, u2, admin):
            await deps.invalidate_membership_cache(uid)
        await close_redis()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _seed(w) -> dict[str, str]:
    """The ledger, through the real write path, each row at its own time; then
    user 1 leaves C. Returns label → the row's ``entity.label``."""
    from app.core import deps
    from app.models.postgres import ProjectMember
    from app.services.activity.service import ActorRef, record

    i, now = w.ids, datetime.now(timezone.utc)
    one, two = ActorRef.from_user(w.users[i.u1]), ActorRef.from_user(w.users[i.u2])
    plan = [
        # label,        event,               actor, project, entity,             minutes ago, context
        ("actor",       "test_case.updated",  one, i.A, i.case_a_assigned, 1, None),
        ("mentioned",   "test_case.commented", two, i.A, i.case_a_authored, 2, {"mentions": [str(i.u1)]}),
        ("assigned",    "test_case.updated",  two, i.A, i.case_a_assigned, 3, None),
        ("authored",    "test_case.updated",  two, i.A, i.case_a_authored, 4, None),
        ("defect",      "defect.updated",     two, i.B, i.defect_b,        5, None),
        ("c_own",       "policy.updated",     one, i.C, uuid.uuid4(),      6, None),
        ("c_mention",   "test_case.commented", two, i.C, i.case_c,         7, {"mentions": [str(i.u1)]}),
        ("c_assigned",  "test_case.updated",  two, i.C, i.case_c,          8, None),
        ("admin_c",     "test_case.updated",  two, i.C, i.case_c_admin,    9, None),
        ("not_mine",    "policy.updated",     two, i.A, uuid.uuid4(),      10, None),
        ("old",         "policy.updated",     one, i.A, uuid.uuid4(),      8 * 24 * 60, None),
    ]
    labels: dict[str, str] = {}
    async with w.sessions() as db:
        for label, event_type, actor, project_id, entity_id, minutes, context in plan:
            labels[label] = f"{label} {w.tag}"
            await record(
                db, project_id=project_id, event_type=event_type, actor=actor,
                entity_id=entity_id, entity_label=labels[label], changed_fields=["priority"],
                context=context, occurred_at=now - timedelta(minutes=minutes),
            )
        await db.commit()
        await db.execute(delete(ProjectMember).where(
            ProjectMember.project_id == i.C, ProjectMember.user_id == i.u1))
        await db.commit()
    await deps.invalidate_membership_cache(i.u1)
    return labels


async def _mine(w, uid, expect: int = 200, **params) -> dict:
    response = await w.http.get("/api/v1/activity/mine", params=params, headers=w.auth[uid])
    assert response.status_code == expect, response.text
    return response.json()


def _pairs(answer: dict, labels: dict[str, str]) -> list[tuple[str, str]]:
    by_label = {v: k for k, v in labels.items()}
    return [(by_label.get(i["entity"]["label"], i["entity"]["label"]), i["involvement"]) for i in answer["items"]]


async def test_user_one_sees_their_rows_and_what_involves_them_and_nothing_of_c(world):
    w = world
    labels = await _seed(w)
    answer = await _mine(w, w.ids.u1)
    assert _pairs(answer, labels) == [
        ("actor", "actor"),           # also in C (assigned): listed ONCE, as the higher priority
        ("mentioned", "mentioned"),   # also authored: the mention wins
        ("assigned", "assigned"),
        ("authored", "authored"),
        ("defect", "assigned"),
    ]
    assert answer["total"] == 5 and answer["projects"] == 2 and answer["next_cursor"] is None
    project_ids = {i["project_id"] for i in answer["items"]}
    assert str(w.ids.C) not in project_ids, "never a row from a project user 1 is not a member of"

    assert _pairs(await _mine(w, w.ids.u1, view="mine"), labels) == [("actor", "actor")]
    assert _pairs(await _mine(w, w.ids.u1, view="involving"), labels) == [
        ("mentioned", "mentioned"), ("assigned", "assigned"), ("authored", "authored"), ("defect", "assigned"),
    ]
    longer = await _mine(w, w.ids.u1, view="mine", since=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat())
    assert _pairs(longer, labels) == [("actor", "actor"), ("old", "actor")]


async def test_three_pages_reproduce_the_unpaged_order_and_total_ignores_the_cursor(world):
    w = world
    labels = await _seed(w)
    unpaged = [i["id"] for i in (await _mine(w, w.ids.u1))["items"]]
    paged: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = await _mine(w, w.ids.u1, **params)
        assert page["total"] == 5
        paged += [i["id"] for i in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages == 3 and paged == unpaged and len(set(paged)) == 5
    assert labels


async def test_the_pin_the_refusals_and_the_tampered_cursor(world):
    w = world
    labels = await _seed(w)
    pinned = await _mine(w, w.ids.u1, project_id=str(w.ids.B))
    assert _pairs(pinned, labels) == [("defect", "assigned")] and pinned["projects"] == 1

    refused = await _mine(w, w.ids.u1, expect=403, project_id=str(w.ids.C))
    assert refused == {"detail": "You do not have access to this project"}
    assert (await _mine(w, w.ids.u1, expect=422, view="nonsense"))["detail"] == (
        "Unknown view. Valid: all, mine, involving."
    )
    theirs = (await _mine(w, w.ids.u2, limit=1))["next_cursor"]
    assert theirs
    tampered = await _mine(w, w.ids.u1, expect=400, cursor=theirs)
    assert tampered == {"detail": "Cursor was issued for a different user"}


async def test_admin_sees_involvement_in_every_project(world):
    w = world
    labels = await _seed(w)
    answer = await _mine(w, w.ids.admin, view="involving")
    assert ("admin_c", "assigned") in _pairs(answer, labels), "C, where the admin is not a member"
    assert answer["projects"] is None


async def test_explain_uses_the_0238_indexes(world):
    from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg

    from app.models.postgres import ProjectActivityEvent as M
    from app.services.activity import mine

    from sqlalchemy import insert

    from app.models.postgres import ManagedTestCase

    w = world
    await _seed(w)
    # Enough cases written and held by someone else that "mine" is selective, then
    # fresh statistics: on four rows any plan is as cheap as any other.
    async with w.sessions.begin() as db:
        await db.execute(insert(ManagedTestCase), [
            {"id": uuid.uuid4(), "project_id": w.ids.A, "title": f"Filler {n} {w.tag}", "author_id": w.ids.u2}
            for n in range(400)
        ])
    async with w.sessions.begin() as db:
        await db.execute(text("ANALYZE managed_test_cases"))
        await db.execute(text("ANALYZE project_activity_events"))
    filters = mine.MyActivityFilters(view="all")
    # Unscoped (ADMIN's read): on a few test rows a scoped part C may start from
    # the project index instead; the assignee / author pair is what must exist.
    parts = mine._part_statements(
        w.ids.u1, filters, scope=None, member_projects={w.ids.A, w.ids.B}, columns="rows"
    )
    plans = {}
    async with w.sessions() as db:
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        for part in ("A", "C"):
            stmt = parts[part].order_by(M.occurred_at.desc(), M.id.desc()).limit(51)
            sql = str(stmt.compile(dialect=pg_asyncpg.dialect(), compile_kwargs={"literal_binds": True}))
            plans[part] = "\n".join(r[0] for r in (await db.execute(text("EXPLAIN " + sql))).all())
        await db.rollback()
    assert "ix_pae_actor_time" in plans["A"], plans["A"]
    assert "ix_mtc_assignee" in plans["C"] or "ix_mtc_author" in plans["C"], plans["C"]
