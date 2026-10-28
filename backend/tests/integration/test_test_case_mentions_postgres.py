"""W21d ACT-02: ``@mentions`` on a test-case comment, end to end on real PostgreSQL.

Through the real app (``app.main.app`` over ``ASGITransport``, JWTs, the real
guard chain) against real rows:

* Bob comments ``@alice please re-check step 3`` -> ONE ``test_case.commented``
  ledger row (``context.mentions == [alice.id]``) and ONE in-app
  ``NotificationLog`` for Alice, committed with the comment;
* the delivery relay's own claim (``claim_pending_notification_deliveries``)
  returns NOTHING for it -- the no-mail proof (the homelab SMTP is a live
  Gmail account), run inside a transaction that is rolled back;
* Alice's bell (``GET /api/v1/notifications/history``) lists it with
  ``link == "/test-management?case=<id>"``;
* Alice's Inbox read (``GET /api/v1/activity/mine?view=involving``) lists the
  row as ``mentioned`` (G-B's route);
* a member of ANOTHER project only, an inactive, a synthetic and a service
  member, an unknown name and the author themself are never mentioned;
* a ``parent_id`` naming a comment of another case is the 404 sentence and
  writes nothing.

The fixture awaits ``dispose_engine_for_loop()`` before building its engine
and after disposing it, and closes the app's Redis / Mongo clients, so the
next module never meets "Event loop is closed". Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head; skips otherwise.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOT_FOUND = "Parent comment not found on this test case."


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _letters(n: int) -> str:
    """A tag of letters only: never a run of digits a redaction pass could read as a phone number."""
    return uuid.uuid4().hex[:n].translate(str.maketrans("0123456789", "ghijklmnop"))


@pytest.fixture
async def world():
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.mongo import close_mongo
    from app.db.postgres import get_db
    from app.db.redis_client import close_redis
    from app.main import app
    from app.models.postgres import ManagedTestCase, Project, ProjectMember, TestCaseComment, User

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=3, max_overflow=0)
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
    app.dependency_overrides[get_db] = _get_db
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)

    tag = "m" + _letters(7)
    P, Q = uuid.uuid4(), uuid.uuid4()
    people = {
        # name: (global role, project, is_active, is_synthetic, is_service_account)
        "alice": ("QA_ENGINEER", P, True, False, False),
        "bob": ("TESTER", P, True, False, False),
        "carol": ("QA_ENGINEER", Q, True, False, False),     # a member of the OTHER project only
        "erin": ("QA_ENGINEER", P, False, False, False),     # inactive
        "sam": ("QA_LEAD", P, True, True, False),            # synthetic
        "svc": ("QA_ENGINEER", P, True, False, True),        # service account
    }
    ids = {name: uuid.uuid4() for name in people}
    case_p, case_q = uuid.uuid4(), uuid.uuid4()
    foreign_comment = uuid.uuid4()
    try:
        async with sessions.begin() as db:
            for pid, label in ((P, "p"), (Q, "q")):
                db.add(Project(id=pid, name=f"mention-{label}-{tag}", slug=f"mention-{label}-{tag}", is_active=True))
            for name, (role, _pid, active, synthetic, service) in people.items():
                db.add(User(
                    id=ids[name], email=f"{name}-{tag}@example.test", username=f"{name}_{tag}",
                    full_name=f"{name.title()} Tester", hashed_password="!unusable", role=role,
                    is_active=active, is_synthetic=synthetic, is_service_account=service,
                ))
            await db.flush()
            for name, (role, pid, *_flags) in people.items():
                db.add(ProjectMember(project_id=pid, user_id=ids[name], role=role))
            db.add(ManagedTestCase(id=case_p, project_id=P, title=f"Login works {tag}"))
            db.add(ManagedTestCase(id=case_q, project_id=Q, title=f"Billing totals {tag}"))
            await db.flush()
            db.add(TestCaseComment(id=foreign_comment, test_case_id=case_q, author_id=ids["carol"], content="Q side"))
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(
                sessions=sessions, client=client, tag=tag, P=P, Q=Q, ids=ids,
                case_p=case_p, case_q=case_q, foreign_comment=foreign_comment,
                jwt=lambda name: {"Authorization": f"Bearer {create_access_token(str(ids[name]))}"},
                username=lambda name: f"{name}_{tag}",
            )
        finally:
            await client.aclose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        users = list(ids.values())
        for statement, params in (
            (text("DELETE FROM notification_logs WHERE project_id = ANY(:ids) OR user_id = ANY(:users)"),
             {"ids": [P, Q], "users": users}),
            (text("DELETE FROM project_activity_events WHERE project_id = ANY(:ids)"), {"ids": [P, Q]}),
            (text("DELETE FROM managed_test_cases WHERE project_id = ANY(:ids)"), {"ids": [P, Q]}),
            (text("DELETE FROM projects WHERE id = ANY(:ids)"), {"ids": [P, Q]}),
            (text("DELETE FROM access_audit_logs WHERE actor_user_id = ANY(:users)"), {"users": users}),
            (delete(User).where(User.id.in_(users)), {}),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement, params)
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"mentions teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        await close_redis()
        await close_mongo()


async def _rows(w, model, *where):
    async with w.sessions() as db:
        return list((await db.execute(select(model).where(*where))).scalars().all())


async def _post(w, who, body, case=None):
    return await w.client.post(
        f"/api/v1/test-management/cases/{case or w.case_p}/comments", json=body, headers=w.jwt(who),
    )


async def test_a_mention_is_one_ledger_row_and_one_in_app_row_that_no_relay_can_claim(world):
    from app.models.postgres import NotificationLog, ProjectActivityEvent
    from app.services.notification.manager import claim_pending_notification_deliveries

    w = world
    alice = w.ids["alice"]
    content = (
        f"@{w.username('alice')} please re-check step 3 "
        f"(cc @{w.username('carol')} @{w.username('erin')} @{w.username('sam')} @{w.username('svc')} "
        f"@{w.username('bob')} @nobody_{w.tag})"
    )
    resp = await _post(w, "bob", {"content": content, "step_number": 3})
    assert resp.status_code == 201, resp.text
    answer = resp.json()
    assert answer["mentions"] == [
        {"user_id": str(alice), "username": w.username("alice"), "display_name": "Alice Tester"},
    ]
    comment_id = uuid.UUID(answer["id"])

    [event] = await _rows(w, ProjectActivityEvent, ProjectActivityEvent.project_id == w.P,
                          ProjectActivityEvent.event_type == "test_case.commented")
    assert event.context == {"comment_id": str(comment_id), "mentions": [str(alice)], "mention_count": 1,
                             "step_number": 3}
    assert event.entity_type == "test_case" and event.entity_id == str(w.case_p)
    assert event.source_table == "test_case_comments" and event.source_id == comment_id
    assert event.actor_id == w.ids["bob"] and event.actor_type == "user"

    notes = await _rows(w, NotificationLog, NotificationLog.user_id.in_(list(w.ids.values())))
    assert [(n.user_id, n.project_id) for n in notes] == [(alice, w.P)]
    [note] = notes
    assert note.channel == "in_app" and note.event_type == "test_case.mentioned"
    assert note.status == "sent" and note.sent_at is not None and note.is_read is False
    assert note.title == f"Bob Tester mentioned you on Login works {w.tag}"
    assert note.delivery_metadata == {"app_path": f"/test-management?case={w.case_p}", "comment_id": str(comment_id)}

    # The no-mail proof: the relay's own claim never selects it (rolled back: no lease is left on anything).
    async with w.sessions() as db:
        claimed = await claim_pending_notification_deliveries(db, limit=500)
        assert note.id not in {row.id for row in claimed}
        await db.rollback()
    [again] = await _rows(w, NotificationLog, NotificationLog.id == note.id)
    assert again.status == "sent" and again.delivery_token is None and again.delivery_attempts == 0


async def test_alice_reads_the_mention_in_her_bell_with_the_case_link(world):
    w = world
    resp = await _post(w, "bob", {"content": f"@{w.username('alice')} please re-check step 3"})
    assert resp.status_code == 201, resp.text
    bell = await w.client.get("/api/v1/notifications/history", headers=w.jwt("alice"))
    assert bell.status_code == 200, bell.text
    [entry] = [e for e in bell.json() if e["event_type"] == "test_case.mentioned"]
    assert entry["link"] == f"/test-management?case={w.case_p}"
    assert entry["channel"] == "in_app" and entry["status"] == "sent" and entry["is_read"] is False
    assert entry["title"] == f"Bob Tester mentioned you on Login works {w.tag}"
    assert entry["body"] == f"@{w.username('alice')} please re-check step 3"
    # Carol (another project only) has no entry; Bob mentioned nobody else.
    for other in ("carol", "bob"):
        theirs = await w.client.get("/api/v1/notifications/history", headers=w.jwt(other))
        assert theirs.status_code == 200
        assert [e for e in theirs.json() if e["event_type"] == "test_case.mentioned"] == []


async def test_alice_sees_the_row_in_inbox_activity_as_mentioned(world):
    """G-B's ``GET /api/v1/activity/mine`` (part B reads ``context.mentions``)."""
    from app.models.postgres import ProjectActivityEvent

    w = world
    resp = await _post(w, "bob", {"content": f"@{w.username('alice')} please re-check step 3"})
    assert resp.status_code == 201, resp.text
    [event] = await _rows(w, ProjectActivityEvent, ProjectActivityEvent.project_id == w.P,
                          ProjectActivityEvent.event_type == "test_case.commented")
    mine = await w.client.get("/api/v1/activity/mine", params={"view": "involving"}, headers=w.jwt("alice"))
    assert mine.status_code == 200, mine.text
    items = {item["id"]: item for item in mine.json()["items"]}
    assert items[str(event.id)]["involvement"] == "mentioned"
    assert items[str(event.id)]["event_type"] == "test_case.commented"


async def test_a_comment_without_a_mention_is_one_ledger_row_and_no_notification(world):
    from app.models.postgres import NotificationLog, ProjectActivityEvent

    w = world
    resp = await _post(w, "bob", {"content": "Looks right"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["mentions"] == []
    [event] = await _rows(w, ProjectActivityEvent, ProjectActivityEvent.project_id == w.P,
                          ProjectActivityEvent.event_type == "test_case.commented")
    assert event.context["mentions"] == [] and event.context["mention_count"] == 0
    assert await _rows(w, NotificationLog, NotificationLog.user_id.in_(list(w.ids.values()))) == []


async def test_a_parent_from_another_case_is_404_and_writes_nothing(world):
    from app.models.postgres import NotificationLog, ProjectActivityEvent, TestCaseComment

    w = world
    resp = await _post(w, "bob", {"content": f"@{w.username('alice')} replying", "parent_id": str(w.foreign_comment)})
    assert resp.status_code == 404
    assert resp.json()["detail"] == NOT_FOUND
    missing = await _post(w, "bob", {"content": "replying", "parent_id": str(uuid.uuid4())})
    assert missing.status_code == 404 and missing.json()["detail"] == NOT_FOUND
    async with w.sessions() as db:
        on_p = await db.scalar(select(func.count()).select_from(TestCaseComment)
                               .where(TestCaseComment.test_case_id == w.case_p))
    assert on_p == 0
    assert await _rows(w, ProjectActivityEvent, ProjectActivityEvent.project_id == w.P) == []
    assert await _rows(w, NotificationLog, NotificationLog.user_id.in_(list(w.ids.values()))) == []
    # A reply to a comment of THIS case is fine.
    first = await _post(w, "bob", {"content": "first"})
    reply = await _post(w, "alice", {"content": "agreed", "parent_id": first.json()["id"]})
    assert reply.status_code == 201, reply.text
    assert reply.json()["parent_id"] == first.json()["id"]
