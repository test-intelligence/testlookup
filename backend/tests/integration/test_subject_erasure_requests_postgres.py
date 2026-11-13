"""W38 (W31 F-1): the self-service erasure request and the administrators' queue, on real PostgreSQL (contract §6 G-A).

The real routers (``subject_erasure_requests``, ``subject_erasure``, ``notifications``) on a bare FastAPI app over
this module's own engine (``get_db`` / ``get_current_active_user`` overridden; the session-only dependency and
``require_role`` real), against real rows:

* filing → ONE request, ONE ``erasure_request_filed`` row and a bell row for every OTHER active human administrator
  (never the requester, an inactive, erased, service or synthetic admin); the bell rows are ``status="sent"``,
  so the delivery relay's own claim (``claim_pending_notification_deliveries``) never selects them, no row of ours
  is ``pending`` and ``aiosmtplib.send`` is never called — the structural no-mail proof (the reference K3s install SMTP is real);
* two concurrent filings by one person → one row + 409 ``request_pending`` naming it; two administrators filing at
  the same moment (both holding their own row) → no deadlock, each notifies the other;
* withdraw, then file again; decline → the person's bell, read through ``GET /api/v1/notifications/history`` with
  ``link == "/settings/profile"`` (the admins' with ``/users?tab=erasure-requests``);
* the erasure of a person with a pending request accepts it with the job in the SAME commit (a failure after the
  close rolls the lock-out back too);
* the only active administrator is refused ``last_admin_self`` (other admins deactivated inside a transaction that
  is rolled back, so a shared CI database is never changed).

Fixture rules: ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (≥ 0256); ``dispose_engine_for_loop()`` before and
after; every row this module seeded or caused (users, requests, jobs, audit rows, bell rows, revocation cutoffs) is
deleted by id, and the touched tables are back to their pre-test counts.
"""
from __future__ import annotations

import asyncio
import os
import random
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_LETTERS = "ghijkmnopqrstuvwxyz"   # never a hex digit
_TABLES = ("users", "subject_erasure_requests", "subject_rights_jobs", "access_audit_logs", "notification_logs",
           "auth_token_revocations")


def _tag(n: int = 8) -> str:
    return "".join(random.choice(_LETTERS) for _ in range(n))


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def sessions() -> Any:
    from app.db import postgres as app_postgres

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=6, max_overflow=0)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _counts(db: Any) -> dict[str, int]:
    return {t: int((await db.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one()) for t in _TABLES}


@pytest_asyncio.fixture(loop_scope="module")
async def world(sessions: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Seven people (``<name>_<tag>``): a person, two active admins, and an inactive, an erased, a service and a
    synthetic admin. ``aiosmtplib.send`` is replaced by a recorder; the erasure's enqueue and cache drop by
    recorders too."""
    from datetime import datetime, timezone

    import aiosmtplib

    from app.models.postgres import User
    from app.routers import subject_erasure as erasure_routes

    tag = "w" + _tag(7)
    people = {
        # name: (role, is_active, erased, is_service_account, is_synthetic)
        "pat": ("TESTER", True, False, False, False),
        "ada": ("ADMIN", True, False, False, False),
        "bea": ("ADMIN", True, False, False, False),
        "off": ("ADMIN", False, False, False, False),
        "gone": ("ADMIN", False, True, False, False),
        "bot": ("ADMIN", True, False, True, False),
        "syn": ("ADMIN", True, False, False, True),
    }
    ids = {name: uuid.uuid4() for name in people}
    mailed: list[Any] = []
    dispatched: list[str] = []

    async def _no_mail(*args: Any, **kwargs: Any) -> None:
        mailed.append((args, kwargs))

    async def _no_cache(user_id: Any) -> None:
        return None

    monkeypatch.setattr(aiosmtplib, "send", _no_mail)
    monkeypatch.setattr("app.services.notification.email_service.aiosmtplib.send", _no_mail)
    monkeypatch.setattr(erasure_routes, "_dispatch", lambda job_id: dispatched.append(str(job_id)))
    monkeypatch.setattr(erasure_routes, "invalidate_membership_cache", _no_cache)
    async with sessions() as db:
        before = await _counts(db)
        for name, (role, active, erased, service, synthetic) in people.items():
            db.add(User(
                id=ids[name], email=f"{name}-{tag}@example.test", username=f"{name}_{tag}",
                full_name=f"{name.title()} {tag.title()}", hashed_password="!unusable", role=role, is_active=active,
                erased_at=datetime.now(timezone.utc) if erased else None, is_service_account=service,
                is_synthetic=synthetic,
            ))
        await db.commit()
    w = SimpleNamespace(sessions=sessions, tag=tag, ids=ids, mailed=mailed, dispatched=dispatched)
    try:
        yield w
    finally:
        await _cleanup(sessions, list(ids.values()))
        async with sessions() as db:
            assert await _counts(db) == before, "a row this module seeded or caused was left behind"


async def _cleanup(sessions: Any, users: list[uuid.UUID]) -> None:
    for statement in (
        "DELETE FROM notification_logs WHERE user_id = ANY(:u) "
        "OR delivery_metadata->>'request_id' IN (SELECT id::text FROM subject_erasure_requests "
        "WHERE subject_user_id = ANY(:u))",
        "DELETE FROM access_audit_logs WHERE actor_user_id = ANY(:u) OR target_user_id = ANY(:u)",
        "DELETE FROM subject_erasure_requests WHERE subject_user_id = ANY(:u) OR decided_by_id = ANY(:u)",
        "DELETE FROM subject_rights_jobs WHERE subject_user_id = ANY(:u)",
        "DELETE FROM auth_token_revocations WHERE user_id = ANY(:u)",
        "DELETE FROM refresh_token_records WHERE user_id = ANY(:u)",
        "DELETE FROM users WHERE id = ANY(:u)",
    ):
        try:
            async with sessions.begin() as db:
                await db.execute(text(statement), {"u": users})
        except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
            print(f"erasure-request teardown: {type(exc).__name__}: {str(exc)[:160]}")


async def _client(w: Any, name: str) -> Any:
    """A client signed in (a session, never an API key) as ``name``, over the three real routers."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.models.postgres import User
    from app.routers import notifications, subject_erasure, subject_erasure_requests

    app = FastAPI()
    for router in (subject_erasure_requests.router, subject_erasure.router, notifications.router):
        app.include_router(router)

    async def _db() -> Any:
        async with w.sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async with w.sessions() as db:
        user = await db.get(User, w.ids[name])
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _rows(w: Any, sql: str, **params: Any) -> list[Any]:
    async with w.sessions() as db:
        return list((await db.execute(text(sql), params)).mappings().all())


async def _human_admins_except(w: Any, requester: uuid.UUID) -> set[uuid.UUID]:
    """Every active, non-erased, human ADMIN of the database but the requester (a shared CI database may hold
    others: they are notified too, and cleaned up by request id)."""
    from app.models.postgres import User, UserRole

    async with w.sessions() as db:
        result = await db.execute(select(User.id).where(
            User.role == UserRole.ADMIN, User.is_active.is_(True), User.erased_at.is_(None),
            User.is_service_account.is_(False), User.is_synthetic.is_(False), User.id != requester,
        ))
        return set(result.scalars().all())


async def _assert_never_mailable(w: Any, request_ids: list[str]) -> None:
    """The relay's own claim (rolled back: no lease is left anywhere) selects none of our bell rows; none is
    pending; nothing reached the SMTP client."""
    from app.services.notification.manager import claim_pending_notification_deliveries

    ours = await _rows(w, "SELECT id, status FROM notification_logs WHERE delivery_metadata->>'request_id' = ANY(:r)",
                       r=request_ids)
    assert ours and {row["status"] for row in ours} == {"sent"}
    async with w.sessions() as db:
        claimed = await claim_pending_notification_deliveries(db, limit=500)
        assert not {row.id for row in claimed} & {row["id"] for row in ours}
        await db.rollback()
    assert w.mailed == []


# ── filing: the request, its audit row, the other admins' bells — never mailed ──

async def test_filing_notifies_every_other_admin_in_the_bell_and_can_never_be_mailed(world: Any) -> None:
    w = world
    async with await _client(w, "pat") as client:
        filed = await client.post("/api/v1/me/erasure-request")
    assert filed.status_code == 201, filed.text
    request_id = filed.json()["id"]
    (row,) = await _rows(w, "SELECT * FROM subject_erasure_requests WHERE subject_user_id = :p", p=w.ids["pat"])
    assert str(row["id"]) == request_id and row["status"] == "pending" and row["decided_at"] is None
    audit = await _rows(w, "SELECT action, actor_user_id, after_value FROM access_audit_logs WHERE target_user_id = :p",
                        p=w.ids["pat"])
    assert [(a["action"], a["actor_user_id"], a["after_value"]) for a in audit] == [
        ("erasure_request_filed", w.ids["pat"], {"request_id": request_id})]
    bells = await _rows(w, "SELECT * FROM notification_logs WHERE delivery_metadata->>'request_id' = :r", r=request_id)
    recipients = {b["user_id"] for b in bells}
    assert recipients == await _human_admins_except(w, w.ids["pat"])
    assert {w.ids["ada"], w.ids["bea"]} <= recipients
    assert not recipients & {w.ids[n] for n in ("pat", "off", "gone", "bot", "syn")}
    for bell in bells:
        assert (bell["channel"], bell["event_type"], bell["status"]) == ("in_app", "erasure_request", "sent")
        assert bell["title"] == "An erasure request is waiting for review" and w.tag not in bell["body"]
        assert w.tag.title() not in bell["title"] and bell["project_id"] is None
        assert bell["delivery_metadata"]["app_path"] == "/users?tab=erasure-requests"
    await _assert_never_mailable(w, [request_id])
    async with await _client(w, "ada") as client:
        history = (await client.get("/api/v1/notifications/history")).json()
    (mine,) = [n for n in history if n["title"] == "An erasure request is waiting for review"]
    assert mine["link"] == "/users?tab=erasure-requests"


async def test_two_tabs_filing_at_once_make_one_request(world: Any) -> None:
    w = world
    async with await _client(w, "pat") as first, await _client(w, "pat") as second:
        answers = await asyncio.gather(first.post("/api/v1/me/erasure-request"),
                                       second.post("/api/v1/me/erasure-request"))
    codes = sorted(a.status_code for a in answers)
    assert codes == [201, 409], [a.text for a in answers]
    won = next(a for a in answers if a.status_code == 201).json()["id"]
    lost = next(a for a in answers if a.status_code == 409).json()["detail"]
    assert lost["code"] == "request_pending" and lost["request_id"] == won
    rows = await _rows(w, "SELECT id FROM subject_erasure_requests WHERE subject_user_id = :p", p=w.ids["pat"])
    assert [str(r["id"]) for r in rows] == [won]
    filed = await _rows(w, "SELECT 1 FROM access_audit_logs WHERE action = 'erasure_request_filed' "
                           "AND target_user_id = :p", p=w.ids["pat"])
    assert len(filed) == 1


async def test_two_admins_filing_at_the_same_moment_never_deadlock(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both filings hold their own ``users`` row and wait for each other (a barrier) before staging the bell rows,
    each of which references the OTHER admin: the FK check's ``FOR KEY SHARE`` must not wait on the other's row
    lock (``FOR NO KEY UPDATE``) — with ``FOR UPDATE`` this exact interleaving deadlocks."""
    from app.services import subject_erasure_request_service as svc

    w = world
    barrier = asyncio.Barrier(2)
    original = svc.stage_admin_bells

    async def _meet_then_bell(db: Any, *, request: Any) -> int:
        await asyncio.wait_for(barrier.wait(), timeout=10)
        return await original(db, request=request)

    monkeypatch.setattr(svc, "stage_admin_bells", _meet_then_bell)
    async with await _client(w, "ada") as ada, await _client(w, "bea") as bea:
        answers = await asyncio.wait_for(asyncio.gather(
            ada.post("/api/v1/me/erasure-request"), bea.post("/api/v1/me/erasure-request")), timeout=30)
    assert [a.status_code for a in answers] == [201, 201], [a.text for a in answers]
    ada_id, bea_id = (a.json()["id"] for a in answers)
    to_bea = await _rows(w, "SELECT user_id FROM notification_logs WHERE delivery_metadata->>'request_id' = :r",
                         r=ada_id)
    to_ada = await _rows(w, "SELECT user_id FROM notification_logs WHERE delivery_metadata->>'request_id' = :r",
                         r=bea_id)
    assert w.ids["bea"] in {r["user_id"] for r in to_bea} and w.ids["ada"] not in {r["user_id"] for r in to_bea}
    assert w.ids["ada"] in {r["user_id"] for r in to_ada} and w.ids["bea"] not in {r["user_id"] for r in to_ada}


# ── withdraw, decline (the person's bell) ────────────────────────────────────

async def test_withdraw_then_ask_again_then_a_decline_reaches_the_persons_bell(world: Any) -> None:
    w = world
    async with await _client(w, "pat") as pat:
        first = (await pat.post("/api/v1/me/erasure-request")).json()["id"]
        withdrawn = await pat.post("/api/v1/me/erasure-request/withdraw")
        again = await pat.post("/api/v1/me/erasure-request")
    assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "withdrawn"
    assert again.status_code == 201 and again.json()["id"] != first
    second = again.json()["id"]
    async with await _client(w, "ada") as ada:
        queue = (await ada.get("/api/v1/users/erasure-requests", params={"status": "pending"})).json()
        assert second in [item["id"] for item in queue["items"]] and first not in [i["id"] for i in queue["items"]]
        declined = await ada.post(f"/api/v1/users/erasure-requests/{second}/decline",
                                  json={"decline_code": "identity_unconfirmed"})
        bad = await ada.post(f"/api/v1/users/erasure-requests/{first}/decline", json={"decline_code": "other"})
    assert declined.status_code == 200 and declined.json()["status"] == "declined"
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "request_not_pending"
    rows = {str(r["id"]): r for r in await _rows(
        w, "SELECT * FROM subject_erasure_requests WHERE subject_user_id = :p", p=w.ids["pat"])}
    assert rows[first]["status"] == "withdrawn" and rows[first]["decided_by_id"] is None
    assert rows[second]["decline_code"] == "identity_unconfirmed" and rows[second]["decided_by_id"] == w.ids["ada"]
    actions = [a["action"] for a in await _rows(
        w, "SELECT action FROM access_audit_logs WHERE target_user_id = :p ORDER BY created_at, action",
        p=w.ids["pat"])]
    assert sorted(actions) == sorted(["erasure_request_filed", "erasure_request_withdrawn", "erasure_request_filed",
                                      "erasure_request_declined"])
    async with await _client(w, "pat") as pat:
        mine = (await pat.get("/api/v1/me/erasure-request")).json()
        history = (await pat.get("/api/v1/notifications/history")).json()
    assert mine["status"] == "declined"
    assert mine["decline_message"] == "We could not confirm the request came from you. Ask your administrator."
    (bell,) = [n for n in history if n["title"] == "Your erasure request was declined"]
    assert bell["link"] == "/settings/profile" and bell["body"] == mine["decline_message"]
    assert (bell["channel"], bell["event_type"], bell["status"]) == ("in_app", "erasure_request.declined", "sent")
    await _assert_never_mailable(w, [first, second])


# ── D-5: the erasure accepts the pending request in its ONE commit ───────────

async def test_erasing_a_person_with_a_pending_request_accepts_it_in_the_same_commit(
    world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import subject_erasure_request_service as svc

    w = world
    async with await _client(w, "pat") as pat:
        request_id = (await pat.post("/api/v1/me/erasure-request")).json()["id"]
    async with await _client(w, "ada") as ada:
        preview = (await ada.get(f"/api/v1/users/{w.ids['pat']}/erasure-preview")).json()
    assert preview["pending_request"]["id"] == request_id
    # (1) a failure AFTER the close rolls everything back: same transaction as the lock-out
    original = svc.close_pending_for

    async def _close_then_fail(db: Any, **kwargs: Any) -> Any:
        await original(db, **kwargs)
        raise RuntimeError("injected after the close, before the commit")

    monkeypatch.setattr(svc, "close_pending_for", _close_then_fail)
    async with await _client(w, "ada") as ada:
        with pytest.raises(RuntimeError, match="injected after the close"):
            await ada.post(f"/api/v1/users/{w.ids['pat']}/erasure", json={"confirm_username": f"pat_{w.tag}"})
    (row,) = await _rows(w, "SELECT status, erasure_job_id FROM subject_erasure_requests WHERE id = :r",
                         r=uuid.UUID(request_id))
    assert (row["status"], row["erasure_job_id"]) == ("pending", None)
    jobs = await _rows(w, "SELECT id FROM subject_rights_jobs WHERE subject_user_id = :p", p=w.ids["pat"])
    (user,) = await _rows(w, "SELECT is_active FROM users WHERE id = :p", p=w.ids["pat"])
    assert jobs == [] and user["is_active"] is True and w.dispatched == []
    # (2) the real run: lock-out + job + the request accepted, committed together
    monkeypatch.setattr(svc, "close_pending_for", original)
    async with await _client(w, "ada") as ada:
        erased = await ada.post(f"/api/v1/users/{w.ids['pat']}/erasure", json={"confirm_username": f"pat_{w.tag}"})
    assert erased.status_code == 202, erased.text
    job_id = erased.json()["id"]
    (row,) = await _rows(w, "SELECT * FROM subject_erasure_requests WHERE id = :r", r=uuid.UUID(request_id))
    assert row["status"] == "accepted" and str(row["erasure_job_id"]) == job_id
    assert row["decided_by_id"] == w.ids["ada"] and row["decided_at"] is not None and row["decline_code"] is None
    (user,) = await _rows(w, "SELECT is_active FROM users WHERE id = :p", p=w.ids["pat"])
    assert user["is_active"] is False and w.dispatched == [job_id]
    requested = await _rows(w, "SELECT after_value FROM access_audit_logs WHERE action = 'subject_erasure_requested' "
                               "AND target_user_id = :p", p=w.ids["pat"])
    assert [r["after_value"]["job_id"] for r in requested] == [job_id]
    async with await _client(w, "ada") as ada:
        queue = (await ada.get("/api/v1/users/erasure-requests", params={"status": "decided"})).json()
    (item,) = [i for i in queue["items"] if i["id"] == request_id]
    assert item["status"] == "accepted" and item["erasure_job_id"] == job_id and item["subject_is_active"] is False


# ── the only active administrator ────────────────────────────────────────────

async def test_the_only_active_administrator_is_refused_last_admin_self(world: Any) -> None:
    """Every OTHER active admin of the database is deactivated inside ONE transaction that is rolled back, so a
    shared CI database is never changed; the real ``is_last_active_admin`` query then refuses the filing."""
    from app.services import subject_erasure_request_service as svc

    w = world
    async with w.sessions() as db:
        await db.execute(text(
            "UPDATE users SET is_active = false WHERE role = 'ADMIN' AND is_active AND id <> :me"), {"me": w.ids["ada"]})
        with pytest.raises(svc.RequestRefused) as refused:
            await svc.file_request(db, person_id=w.ids["ada"])
        assert refused.value.code == "last_admin_self"
        assert (await db.execute(select(func.count()).select_from(text("subject_erasure_requests"))
                                 .where(text("subject_user_id = :me")), {"me": w.ids["ada"]})).scalar_one() == 0
        await db.rollback()
    (bea,) = await _rows(w, "SELECT is_active FROM users WHERE id = :b", b=w.ids["bea"])
    assert bea["is_active"] is True
    async with await _client(w, "ada") as ada:   # with another active admin, the same admin may file
        assert (await ada.post("/api/v1/me/erasure-request")).status_code == 201
