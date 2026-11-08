"""W38 (W31 F-1): the self-service erasure request + the administrators' queue (``routers/subject_erasure_requests.py``,
``services/subject_erasure_request_service.py``; contract §2).

The real router on a bare FastAPI app over SQLite (the W31 route-test pattern: ``get_db`` /
``get_current_active_user`` overridden, ``require_role`` and the session-only dependency real; the SQLite world of
``test_subject_erasure_service.py`` with ``uq_ser_one_pending`` rebuilt as SQLite's own partial index). Every route
refuses an API key (403 ``session_required``); the queue is ADMIN-only; filing refuses in §2's order and writes the
row + its audit row + a bell row for every OTHER active human administrator in ONE commit; withdraw; the queue's
order / filters / displays; decline (codes, ``own_request``, the coded 422) + the person's bell; reads write
nothing. The bell rows are ``status="sent"`` (the relay claims only ``pending``) with an event type outside
``NotificationEventType``, and carry no name. Real concurrency and the relay's own claim run on Postgres
(``tests/integration/test_subject_erasure_requests_postgres.py``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import (
    AccessAuditLog,
    NotificationChannel,
    NotificationEventType,
    NotificationLog,
    SubjectErasureRequest,
    User,
    UserRole,
)
from app.routers import subject_erasure_requests as routes
from app.services import subject_erasure_request_service as svc
from app.services.activity import live_vocab
from app.services.notification import gate_notifications
from app.services.subject_rights_vocab import (
    BELL_APP_PATHS,
    BELL_SENTENCES,
    DECLINE_CODES,
    DECLINE_SENTENCES,
    ERROR_SENTENCES,
)
from tests.test_subject_erasure_service import _api_key, _job, _user
from tests.test_subject_erasure_service import maker as _sqlite_maker
from tests.test_subject_erasure_service import world as _sqlite_world

_base_maker = pytest.fixture(name="base_maker")(_sqlite_maker.__wrapped__)  # type: ignore[attr-defined]
world = pytest.fixture(name="world")(_sqlite_world.__wrapped__)  # type: ignore[attr-defined]

ME = "/api/v1/me/erasure-request"
QUEUE = "/api/v1/users/erasure-requests"
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(name="maker")
async def maker(base_maker: Any) -> Any:
    """The SQLite world, with ``uq_ser_one_pending`` as a PARTIAL unique (SQLite ignores ``postgresql_where``, so
    create_all built a full unique on subject_user_id — a person could never file again after a withdrawal)."""
    assert "subject_erasure_requests" in base_maker.created
    async with base_maker.engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS uq_ser_one_pending"))
        await conn.execute(text(
            "CREATE UNIQUE INDEX uq_ser_one_pending ON subject_erasure_requests (subject_user_id) "
            "WHERE status = 'pending'"
        ))
    return base_maker


def _detail(code: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": ERROR_SENTENCES[code], **extra}


def _client(maker: Any, user: Any, commits: list[str]) -> AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)

    async def _db() -> Any:
        async with maker.session() as session:
            original = session.commit

            async def _counted() -> None:
                commits.append("commit")
                await original()

            session.commit = _counted  # type: ignore[method-assign]
            try:
                yield session
                await original()   # get_db's own end-of-request commit (not counted)
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _count(maker: Any, model: Any, *where: Any) -> int:
    async with maker.session() as db:
        return int((await db.execute(select(func.count()).select_from(model).where(*where))).scalar() or 0)


async def _rows(maker: Any, model: Any, *where: Any) -> list[Any]:
    async with maker.session() as db:
        return list((await db.execute(select(model).where(*where))).scalars().all())


async def _add(maker: Any, *rows: Any) -> None:
    async with maker.session() as db:
        db.add_all(rows)
        await db.commit()


def _request(subject: User, *, status: str = "pending", at: datetime = T0, decided: Any = None,
             by: Any = None, code: Any = None) -> SubjectErasureRequest:
    return SubjectErasureRequest(
        id=uuid.uuid4(), subject_user_id=subject.id, status=status, requested_at=at,
        decided_at=decided if decided is not None else (None if status == "pending" else at + timedelta(hours=1)),
        decided_by_id=by.id if by is not None else None, decline_code=code,
    )


# ── who may call ─────────────────────────────────────────────────────────────

async def test_every_route_refuses_an_api_key_before_reading_anything(maker: Any, world: SimpleNamespace) -> None:
    pending = _request(world.subject)
    await _add(maker, pending)
    commits: list[str] = []
    for principal in (_api_key(world.subject), _api_key(world.admin)):
        async with _client(maker, principal, commits) as client:
            answers = [
                await client.get(ME), await client.post(ME), await client.post(f"{ME}/withdraw"),
                await client.get(QUEUE), await client.post(f"{QUEUE}/{pending.id}/decline", json={"decline_code": "other"}),
                await client.post(f"{QUEUE}/{uuid.uuid4()}/decline", json={"decline_code": "other"}),
            ]
        assert [a.status_code for a in answers] == [403] * 6
        assert all(a.json()["detail"] == _detail("session_required") for a in answers)
    assert commits == []
    assert await _count(maker, SubjectErasureRequest) == 1 and await _count(maker, AccessAuditLog) == 0
    assert await _count(maker, NotificationLog) == 0
    assert (await _rows(maker, SubjectErasureRequest))[0].status == "pending"


async def test_the_queue_is_admin_only(maker: Any, world: SimpleNamespace) -> None:
    pending = _request(world.subject)
    await _add(maker, pending)
    lead = _user(UserRole.QA_LEAD, "zqxwqalead")
    await _add(maker, lead)
    commits: list[str] = []
    async with _client(maker, lead, commits) as client:
        listed = await client.get(QUEUE)
        declined = await client.post(f"{QUEUE}/{pending.id}/decline", json={"decline_code": "other"})
    assert listed.status_code == 403 and declined.status_code == 403
    assert commits == [] and (await _rows(maker, SubjectErasureRequest))[0].status == "pending"


# ── filing ───────────────────────────────────────────────────────────────────

def _assert_bell(row: NotificationLog, *, user_id: uuid.UUID, key: str, event: str, title: str, body: str,
                 path: str, request_id: str) -> None:
    assert row.user_id == user_id and row.project_id is None
    assert row.channel == live_vocab.IN_APP_CHANNEL == "in_app" and row.event_type == event
    assert row.status == "sent" and row.sent_at is not None and row.is_read is False   # never "pending": never mailed
    assert (row.title, row.body) == (title, body)
    assert row.delivery_key == key and len(key) == 64
    assert row.delivery_metadata == {"app_path": path, "request_id": request_id}
    assert row.next_delivery_at is None and row.delivery_token is None


async def test_filing_is_one_commit_with_the_audit_row_and_a_bell_for_every_other_admin(
    maker: Any, world: SimpleNamespace,
) -> None:
    admin2 = _user(UserRole.ADMIN, "zqxwadmintwo")
    asleep = _user(UserRole.ADMIN, "zqxwadminoff", is_active=False)
    gone = _user(UserRole.ADMIN, "zqxwadmingone", erased_at=T0)
    robot = _user(UserRole.ADMIN, "zqxwadminbot", is_service_account=True)
    fake = _user(UserRole.ADMIN, "zqxwadminsyn", is_synthetic=True)
    await _add(maker, admin2, asleep, gone, robot, fake)
    commits: list[str] = []
    async with _client(maker, world.subject, commits) as client:
        filed = await client.post(ME)
    assert filed.status_code == 201, filed.text
    body = filed.json()
    assert set(body) == {"id", "status", "requested_at", "decided_at", "decline_code", "decline_message"}
    assert body["status"] == "pending" and body["decided_at"] is None and body["decline_code"] is None
    assert commits == ["commit"]
    (row,) = await _rows(maker, SubjectErasureRequest)
    assert str(row.id) == body["id"] and row.subject_user_id == world.subject.id and row.decided_by_id is None
    (audit,) = await _rows(maker, AccessAuditLog)
    assert (audit.action, audit.actor_user_id, audit.target_user_id) == (
        "erasure_request_filed", world.subject.id, world.subject.id)
    assert audit.after_value == {"request_id": body["id"]} and audit.before_value is None
    bells = sorted(await _rows(maker, NotificationLog), key=lambda r: str(r.user_id))
    expected = sorted([world.admin.id, admin2.id], key=str)
    assert [b.user_id for b in bells] == expected        # never the inactive / erased / machine admins, never the person
    for bell in bells:
        _assert_bell(bell, user_id=bell.user_id, key=svc.waiting_delivery_key(row.id, bell.user_id),
                     event="erasure_request", title="An erasure request is waiting for review",
                     body=BELL_SENTENCES["erasure_request_waiting_body"], path="/users?tab=erasure-requests",
                     request_id=body["id"])
        for name in ("zqxwsubj", "Zqxwsubj"):            # no username or full name of the person, anywhere
            assert name not in bell.title and name not in bell.body and name not in str(bell.delivery_metadata)


async def test_an_admin_files_too_and_is_never_notified_of_their_own_request(maker: Any, world: SimpleNamespace) -> None:
    admin2 = _user(UserRole.ADMIN, "zqxwadmintwo")
    await _add(maker, admin2)
    async with _client(maker, world.admin, []) as client:
        filed = await client.post(ME)
    assert filed.status_code == 201
    assert [b.user_id for b in await _rows(maker, NotificationLog)] == [admin2.id]


async def test_no_other_human_admin_means_zero_bells_and_the_request_still_files(maker: Any, world: SimpleNamespace) -> None:
    # the other active admin is a service account: the filer is not the last active admin, yet nobody gets a bell
    await _add(maker, _user(UserRole.ADMIN, "zqxwadminbot", is_service_account=True))
    async with _client(maker, world.admin, []) as client:
        filed = await client.post(ME)
    assert filed.status_code == 201
    assert await _count(maker, NotificationLog) == 0 and await _count(maker, SubjectErasureRequest) == 1


@pytest.mark.parametrize(
    ("who", "setup", "code", "status"),
    [
        ("bot", None, "not_a_person", 409),
        ("synthetic", None, "not_a_person", 409),
        ("subject", "erased", "user_erased", 409),
        ("subject", "erasure_live", "user_erased", 409),
        ("subject", "pending", "request_pending", 409),
        ("admin", None, "last_admin_self", 409),
        # the order: each earlier refusal wins over a later one
        ("bot", "pending", "not_a_person", 409),
        ("subject", "erased+pending", "user_erased", 409),
        ("admin", "pending", "request_pending", 409),
    ],
)
async def test_filing_refuses_in_order_and_writes_nothing(maker: Any, world: SimpleNamespace, who: str,
                                                          setup: Any, code: str, status: int) -> None:
    person = getattr(world, who)
    live: Any = None
    async with maker.session() as db:
        row = await db.get(User, person.id)
        if setup and "erased" in setup:
            row.erased_at = T0
        if setup == "erasure_live":
            db.add(_job(row, world.admin if who != "admin" else row, kind="erasure", status="running"))
        if setup and "pending" in setup:
            live = _request(row)
            db.add(live)
        await db.commit()
    before = await _count(maker, SubjectErasureRequest)
    commits: list[str] = []
    async with _client(maker, person, commits) as client:
        refused = await client.post(ME)
    assert refused.status_code == status
    extra = {"request_id": str(live.id)} if code == "request_pending" else {}
    assert refused.json()["detail"] == _detail(code, **extra)
    assert commits == [] and await _count(maker, SubjectErasureRequest) == before
    assert await _count(maker, AccessAuditLog) == 0 and await _count(maker, NotificationLog) == 0


async def test_a_lost_race_reads_the_winners_request_as_request_pending(
    maker: Any, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two tabs: the second filing passed the refusals before the first committed (simulated by blinding the
    pending check); the partial unique ``uq_ser_one_pending`` is the backstop → 409 with the winner's id."""
    winner = _request(world.subject)
    await _add(maker, winner)

    async def _blind(db: Any, person: Any) -> None:
        return None

    monkeypatch.setattr(svc, "filing_refusal", _blind)
    commits: list[str] = []
    async with _client(maker, world.subject, commits) as client:
        lost = await client.post(ME)
    assert lost.status_code == 409 and lost.json()["detail"] == _detail("request_pending", request_id=str(winner.id))
    assert commits == [] and await _count(maker, SubjectErasureRequest) == 1
    assert await _count(maker, AccessAuditLog) == 0 and await _count(maker, NotificationLog) == 0


# ── withdraw, and the person's own read ──────────────────────────────────────

async def test_withdraw_is_one_commit_then_the_person_may_ask_again(maker: Any, world: SimpleNamespace) -> None:
    commits: list[str] = []
    async with _client(maker, world.subject, commits) as client:
        none_yet = await client.get(ME)
        filed = await client.post(ME)
        commits.clear()
        withdrawn = await client.post(f"{ME}/withdraw")
        assert commits == ["commit"]
        mine = await client.get(ME)
        again = await client.post(ME)
    assert none_yet.status_code == 200 and none_yet.json() is None
    assert withdrawn.status_code == 200 and withdrawn.json()["id"] == filed.json()["id"]
    assert withdrawn.json()["status"] == "withdrawn" and withdrawn.json()["decided_at"] is not None
    assert mine.json() == withdrawn.json()
    assert again.status_code == 201 and again.json()["id"] != filed.json()["id"]
    first = next(r for r in await _rows(maker, SubjectErasureRequest) if str(r.id) == filed.json()["id"])
    assert first.status == "withdrawn" and first.decided_by_id is None and first.decline_code is None
    audit = [(a.action, a.actor_user_id, a.after_value) for a in await _rows(maker, AccessAuditLog)
             if a.action == "erasure_request_withdrawn"]
    assert audit == [("erasure_request_withdrawn", world.subject.id, {"request_id": filed.json()["id"]})]
    # the withdrawal notifies nobody: 1 bell for the first filing, 1 for the second
    assert await _count(maker, NotificationLog) == 2


async def test_withdraw_without_a_pending_request_is_409(maker: Any, world: SimpleNamespace) -> None:
    await _add(maker, _request(world.subject, status="declined", code="other", by=world.admin))
    commits: list[str] = []
    async with _client(maker, world.subject, commits) as client:
        refused = await client.post(f"{ME}/withdraw")
    assert refused.status_code == 409 and refused.json()["detail"] == _detail("no_pending_request")
    assert commits == [] and await _count(maker, AccessAuditLog) == 0


async def test_my_request_is_the_newest_and_a_decline_reads_its_sentence(maker: Any, world: SimpleNamespace) -> None:
    older = _request(world.subject, status="withdrawn", at=T0)
    newer = _request(world.subject, status="declined", at=T0 + timedelta(days=2), by=world.admin,
                     code="legal_obligation")
    other = _request(world.bot, at=T0 + timedelta(days=5))
    await _add(maker, older, newer, other)
    commits: list[str] = []
    async with _client(maker, world.subject, commits) as client:
        mine = await client.get(ME)
    assert mine.status_code == 200 and commits == []
    assert mine.json()["id"] == str(newer.id) and mine.json()["status"] == "declined"
    assert mine.json()["decline_code"] == "legal_obligation"
    assert mine.json()["decline_message"] == DECLINE_SENTENCES["legal_obligation"]


# ── the queue ────────────────────────────────────────────────────────────────

async def test_the_queue_orders_filters_counts_and_writes_nothing(maker: Any, world: SimpleNamespace) -> None:
    people = [_user(UserRole.TESTER, f"zqxwq{i}") for i in range(5)]
    await _add(maker, *people)
    old_pending = _request(people[0], at=T0)
    new_pending = _request(people[1], at=T0 + timedelta(days=1))
    declined = _request(people[2], status="declined", at=T0, decided=T0 + timedelta(days=2), by=world.admin,
                        code="other")
    accepted = _request(people[3], status="accepted", at=T0, decided=T0 + timedelta(days=4), by=world.admin)
    withdrawn = _request(people[4], status="withdrawn", at=T0, decided=T0 + timedelta(days=3))
    await _add(maker, new_pending, declined, old_pending, accepted, withdrawn)
    commits: list[str] = []
    async with _client(maker, world.admin, commits) as client:
        everything = await client.get(QUEUE)
        pending = await client.get(QUEUE, params={"status": "pending"})
        decided = await client.get(QUEUE, params={"status": "decided"})
        bad = await client.get(QUEUE, params={"status": "open"})
    assert commits == [] and bad.status_code == 422
    ids = lambda r: [item["id"] for item in r.json()["items"]]  # noqa: E731
    order = [old_pending, new_pending, accepted, withdrawn, declined]   # pending oldest first, then newest decided
    assert ids(everything) == [str(r.id) for r in order] and everything.json()["total"] == 5
    assert ids(pending) == [str(old_pending.id), str(new_pending.id)] and pending.json()["total"] == 2
    assert ids(decided) == [str(accepted.id), str(withdrawn.id), str(declined.id)] and decided.json()["total"] == 3
    first = everything.json()["items"][0]
    assert set(first) == {
        "id", "subject_user_id", "subject_display", "subject_username", "subject_is_erased", "subject_is_active",
        "requester_is_you", "status", "requested_at", "decided_at", "decided_by_display", "decline_code",
        "erasure_job_id",
    }
    assert first["subject_display"] == "Zqxwq0" and first["subject_username"] == "zqxwq0"
    assert first["subject_is_active"] is True and first["subject_is_erased"] is False
    assert first["requester_is_you"] is False and first["decided_by_display"] is None
    row = everything.json()["items"][4]
    assert row["decline_code"] == "other" and row["decided_by_display"] == "Zqxwadmin"
    assert all("@" not in str(item.get("subject_display")) for item in everything.json()["items"])


async def test_the_queue_reads_an_erased_person_as_erased_user_and_flags_your_own(maker: Any,
                                                                                  world: SimpleNamespace) -> None:
    gone = _user(UserRole.TESTER, "erased-1a2b3c4d", full_name="Zqxw Stalename", erased_at=T0, is_active=False)
    await _add(maker, gone)
    await _add(maker, _request(gone, status="accepted", by=world.admin), _request(world.admin))
    async with _client(maker, world.admin, []) as client:
        items = (await client.get(QUEUE)).json()["items"]
    mine, erased = items
    assert mine["requester_is_you"] is True and mine["status"] == "pending"
    assert erased["subject_display"] == "Erased user" and erased["subject_is_erased"] is True
    assert erased["subject_is_active"] is False and erased["requester_is_you"] is False


# ── decline ──────────────────────────────────────────────────────────────────

async def test_decline_is_one_commit_with_the_audit_row_and_the_persons_bell(maker: Any, world: SimpleNamespace) -> None:
    pending = _request(world.subject)
    await _add(maker, pending)
    commits: list[str] = []
    async with _client(maker, world.admin, commits) as client:
        declined = await client.post(f"{QUEUE}/{pending.id}/decline", json={"decline_code": "active_contract"})
    assert declined.status_code == 200, declined.text
    assert commits == ["commit"]
    body = declined.json()
    assert body["id"] == str(pending.id) and body["status"] == "declined" and body["decline_code"] == "active_contract"
    assert body["decided_by_display"] == "Zqxwadmin" and body["decided_at"] is not None
    (row,) = await _rows(maker, SubjectErasureRequest)
    assert row.decided_by_id == world.admin.id and row.decline_code == "active_contract"
    (audit,) = await _rows(maker, AccessAuditLog)
    assert (audit.action, audit.actor_user_id, audit.target_user_id) == (
        "erasure_request_declined", world.admin.id, world.subject.id)
    assert audit.after_value == {"request_id": str(pending.id), "decline_code": "active_contract"}
    (bell,) = await _rows(maker, NotificationLog)
    _assert_bell(bell, user_id=world.subject.id, key=svc.declined_delivery_key(pending.id),
                 event="erasure_request.declined", title="Your erasure request was declined",
                 body=DECLINE_SENTENCES["active_contract"], path="/settings/profile", request_id=str(pending.id))
    async with _client(maker, world.subject, []) as client:
        mine = (await client.get(ME)).json()
    assert mine["status"] == "declined" and mine["decline_message"] == DECLINE_SENTENCES["active_contract"]


@pytest.mark.parametrize("payload", [
    {"decline_code": "because"}, {"decline_code": ""}, {"decline_code": None}, {"decline_code": 3}, {},
    {"decline_code": ["other"]}, None,
])
async def test_a_bad_decline_code_is_the_coded_422_and_writes_nothing(maker: Any, world: SimpleNamespace,
                                                                     payload: Any) -> None:
    pending = _request(world.subject)
    await _add(maker, pending)
    commits: list[str] = []
    async with _client(maker, world.admin, commits) as client:
        url = f"{QUEUE}/{pending.id}/decline"
        refused = await (client.post(url) if payload is None else client.post(url, json=payload))
    assert refused.status_code == 422 and refused.json()["detail"] == _detail("decline_code_invalid")
    assert commits == [] and (await _rows(maker, SubjectErasureRequest))[0].status == "pending"
    assert await _count(maker, AccessAuditLog) == 0 and await _count(maker, NotificationLog) == 0


async def test_decline_refusals_in_order(maker: Any, world: SimpleNamespace) -> None:
    admin2 = _user(UserRole.ADMIN, "zqxwadmintwo")
    await _add(maker, admin2)
    own = _request(world.admin)
    decided = _request(world.subject, status="withdrawn")
    own_decided = _request(admin2, status="withdrawn")
    await _add(maker, own, decided, own_decided)
    commits: list[str] = []
    async with _client(maker, world.admin, commits) as client:
        unknown = await client.post(f"{QUEUE}/{uuid.uuid4()}/decline", json={"decline_code": "other"})
        not_pending = await client.post(f"{QUEUE}/{decided.id}/decline", json={"decline_code": "other"})
        mine = await client.post(f"{QUEUE}/{own.id}/decline", json={"decline_code": "other"})
        mine_bad_code = await client.post(f"{QUEUE}/{own.id}/decline", json={"decline_code": "nope"})
        bad_code_decided = await client.post(f"{QUEUE}/{decided.id}/decline", json={"decline_code": "nope"})
    assert unknown.status_code == 404 and unknown.json()["detail"] == _detail("request_not_found")
    assert not_pending.status_code == 409 and not_pending.json()["detail"] == _detail("request_not_pending")
    assert mine.status_code == 409 and mine.json()["detail"] == _detail("own_request")
    assert mine_bad_code.json()["detail"] == _detail("own_request")            # own_request before the code
    assert bad_code_decided.json()["detail"] == _detail("request_not_pending")  # not pending before the code
    assert commits == [] and await _count(maker, AccessAuditLog) == 0 and await _count(maker, NotificationLog) == 0
    async with _client(maker, admin2, []) as client:   # another administrator may decline it
        other_admin = await client.post(f"{QUEUE}/{own.id}/decline", json={"decline_code": "identity_unconfirmed"})
    assert other_admin.status_code == 200 and other_admin.json()["requester_is_you"] is False


# ── the vocabulary of the bell rows (structural: never mailable, always linkable) ──

def test_the_bell_rows_are_outside_every_mailable_vocabulary() -> None:
    events = {live_vocab.ERASURE_REQUEST_NOTIFICATION_EVENT, live_vocab.ERASURE_REQUEST_DECLINED_EVENT}
    assert events == {"erasure_request", "erasure_request.declined"}
    assert not events & {e.value for e in NotificationEventType}   # no preference can subscribe to them
    assert live_vocab.IN_APP_CHANNEL not in {c.value for c in NotificationChannel}
    assert all(len(e) <= 50 for e in events)                       # notification_logs.event_type String(50)
    for path in BELL_APP_PATHS.values():
        assert gate_notifications.app_link({"app_path": path}) == path   # the bell may open it
    assert set(svc.REFUSAL_STATUS) <= set(ERROR_SENTENCES) and tuple(DECLINE_SENTENCES) == DECLINE_CODES
