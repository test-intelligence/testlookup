"""W37 F-W30-6 — the one-time browser download of a large project export (contract §2.4, D-4).

The ticket service against a FAITHFUL in-memory Redis (``SET key value EX ttl NX`` and ``GETDEL`` with redis-py's
return values and expiry on a controllable clock — the real server was exercised once by hand, see the W37 G-C
report); the two routes through the REAL routers on the SQLite harness of ``test_project_data_routes.py`` (its
tables, partial indexes, ``FakeStorage``, job rows and statement recorder), with the Bearer download mounted beside
them so "the same refusal" and "the same bytes and headers" are measured, not assumed. The ticket POST runs as an
overridden ``get_current_active_user``; the ticket GET runs with NO credential override at all.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text, update
from sqlalchemy import types as sqltypes
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.deps import CREDENTIAL_KIND_API_KEY, _bind_api_key_project, _bind_credential_kind, get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import Base, Project, ProjectDataJob, ProjectMember, User, UserRole
from app.routers import project_data as bearer
from app.routers import project_data_download as routes
from app.services import project_data_download_ticket as tickets
from app.services import project_export_vocab as vocab
from tests.test_project_data_routes import (
    _PARTIAL,
    _TABLES,
    NOT_FOUND,
    FakeStorage,
    _AwareDateTime,
    _job,
    _NoRedis,
    _jobs_of_every_kind,
    _user,
    _writes,
)

LINK_GONE = "This download link has expired or was already used. Go back to Project data and click Download again."
TICKET_SENTENCE = "Downloads through the browser are unavailable right now; try again or use the CLI."
_KEY_PREFIX = "testlookup:project-data-download:"


class FakeRedis:
    """``SET … EX … NX`` and ``GETDEL`` as redis-py answers them (``True`` / ``None``), expiring on ``self.now``."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float, int]] = {}      # key -> (value, dies at, the EX given)
        self.now = 0.0
        self.getdels: list[str] = []

    def _live(self, key: str) -> Optional[tuple[str, float, int]]:
        entry = self.store.get(key)
        if entry is not None and entry[1] <= self.now:
            del self.store[key]
            return None
        return entry

    async def set(self, key: str, value: str, ex: Optional[int] = None, nx: bool = False) -> Optional[bool]:
        assert isinstance(ex, int) and ex >= 1, f"EX must be a whole second count, got {ex!r}"
        if nx and self._live(key) is not None:
            return None
        self.store[key] = (value, self.now + ex, ex)
        return True

    async def getdel(self, key: str) -> Optional[str]:
        self.getdels.append(key)
        entry = self._live(key)
        if entry is None:
            return None
        del self.store[key]
        return entry[0]


class DownRedis:
    async def set(self, *_: Any, **__: Any) -> Any:
        raise ConnectionError("redis down")

    async def getdel(self, *_: Any) -> Any:
        raise ConnectionError("redis down")


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(tickets, "get_redis", lambda: fake)
    return fake


@pytest.fixture
async def factory(monkeypatch: pytest.MonkeyPatch) -> Any:
    """``test_project_data_routes.factory``: the four tables on SQLite, every statement recorded."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}

    @event.listens_for(engine.sync_engine, "connect")
    def _no_pysqlite_begin(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN")

    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    monkeypatch.setattr(ProjectDataJob.__table__.c.counts, "server_default", None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
        for name in ("uq_pdj_one_live_export", "uq_pdj_import_token"):
            await conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        for ddl in _PARTIAL:
            await conn.execute(text(ddl))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    setattr(maker, "statements", statements)
    # The membership cache (get_accessible_project_ids) never hits a real Redis: every re-check reads the table.
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: _NoRedis())
    yield maker
    await engine.dispose()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    store = FakeStorage()
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: store)
    return store


@pytest.fixture
async def world(factory: Any) -> SimpleNamespace:
    """``test_project_data_routes.world``: two projects, the roles, a project-bound key."""
    project = Project(id=uuid.uuid4(), name="Payment Service", slug="payment-service", is_active=True)
    other = Project(id=uuid.uuid4(), name="Auth Service", slug="auth-service", is_active=True)
    admin, admin2 = _user(UserRole.ADMIN, "ada"), _user(UserRole.ADMIN, "abe")
    lead, outsider = _user(UserRole.QA_LEAD, "lee"), _user(UserRole.QA_LEAD, "olu")
    engineer, viewer = _user(UserRole.QA_ENGINEER, "eve"), _user(UserRole.VIEWER, "vic")
    async with factory() as db:
        db.add_all([project, other, admin, admin2, lead, outsider, engineer, viewer])
        await db.flush()
        for member, target in ((lead, project), (engineer, project), (viewer, project), (outsider, other)):
            db.add(ProjectMember(user_id=member.id, project_id=target.id, role=member.role))
        await db.commit()
    bound = SimpleNamespace(id=admin.id, role=UserRole.ADMIN.value, is_active=True, username="ci-key",
                            full_name=None, email="ci@example.test")
    _bind_credential_kind(_bind_api_key_project(bound, project.id), CREDENTIAL_KIND_API_KEY)
    return SimpleNamespace(project=project, other=other, admin=admin, admin2=admin2, lead=lead, outsider=outsider,
                           engineer=engineer, viewer=viewer, bound=bound)


def _client(factory: Any, user: Any = None) -> AsyncClient:
    """The Bearer router + both ticket routers. ``user=None``: NO credential override (the ticket GET)."""
    app = FastAPI()
    app.include_router(bearer.router)
    app.include_router(routes.router)
    app.include_router(routes.public_router)

    async def _db() -> Any:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    if user is not None:
        app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _ticket_path(job_id: Any) -> str:
    return f"/api/v1/project-data-jobs/{job_id}/download-ticket"


# ── the ticket service ──────────────────────────────────────────────────────

_NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
_LATER = _NOW + timedelta(hours=71)


async def test_a_ticket_round_trips_to_its_issuer_and_is_stored_only_as_a_hash(redis: FakeRedis) -> None:
    job_id, user_id = uuid.uuid4(), uuid.uuid4()
    ticket, ttl = await tickets.issue_download_ticket(job_id, user_id, expires_at=_LATER, now=_NOW)

    assert ttl == vocab.DOWNLOAD_TICKET_SECONDS == 60 and len(ticket) >= 43
    (key, (payload, _dies, ex)), = redis.store.items()
    assert key == _KEY_PREFIX + hashlib.sha256(ticket.encode("utf-8")).hexdigest() and ex == 60
    assert ticket not in key and ticket not in payload
    assert json.loads(payload) == {"job_id": str(job_id), "user_id": str(user_id)}
    assert await tickets.redeem_download_ticket(ticket, job_id) == user_id


async def test_a_ticket_opens_once(redis: FakeRedis) -> None:
    job_id = uuid.uuid4()
    ticket, _ = await tickets.issue_download_ticket(job_id, uuid.uuid4(), expires_at=_LATER, now=_NOW)

    assert await tickets.redeem_download_ticket(ticket, job_id) is not None
    assert await tickets.redeem_download_ticket(ticket, job_id) is None
    assert redis.store == {}


async def test_another_jobs_ticket_opens_nothing_and_is_spent(redis: FakeRedis) -> None:
    job_a, job_b = uuid.uuid4(), uuid.uuid4()
    ticket, _ = await tickets.issue_download_ticket(job_a, uuid.uuid4(), expires_at=_LATER, now=_NOW)

    assert await tickets.redeem_download_ticket(ticket, job_b) is None
    assert await tickets.redeem_download_ticket(ticket, job_a) is None, "a mis-aimed ticket stays spent"


async def test_a_ticket_dies_after_its_lifetime(redis: FakeRedis) -> None:
    job_id = uuid.uuid4()
    ticket, ttl = await tickets.issue_download_ticket(job_id, uuid.uuid4(), expires_at=_LATER, now=_NOW)
    redis.now += ttl

    assert await tickets.redeem_download_ticket(ticket, job_id) is None


@pytest.mark.parametrize(
    ("left", "ttl"),
    [(timedelta(hours=72), 60), (timedelta(seconds=60), 60), (timedelta(seconds=25.7), 25),
     (timedelta(seconds=1), 1), (timedelta(seconds=0.9), 0), (timedelta(0), 0), (timedelta(seconds=-5), 0)],
)
def test_the_lifetime_is_capped_at_the_exports_expiry(left: timedelta, ttl: int) -> None:
    assert tickets.ticket_ttl(_NOW + left, now=_NOW) == ttl


async def test_the_issued_lifetime_is_the_capped_one(redis: FakeRedis) -> None:
    _, ttl = await tickets.issue_download_ticket(uuid.uuid4(), uuid.uuid4(), expires_at=_NOW + timedelta(seconds=25.7),
                                                 now=_NOW)
    (_, (_, _, ex)), = redis.store.items()
    assert ttl == ex == 25


async def test_an_export_with_under_a_second_left_gets_no_ticket(redis: FakeRedis) -> None:
    with pytest.raises(tickets.ExportExpiring):
        await tickets.issue_download_ticket(uuid.uuid4(), uuid.uuid4(), expires_at=_NOW + timedelta(seconds=0.9),
                                            now=_NOW)
    assert redis.store == {}


async def test_a_collision_draws_a_new_ticket(redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    taken, fresh = "c" * 43, "f" * 43
    redis.store[tickets._key(taken)] = ("occupied", float("inf"), 60)
    drawn = iter((taken, fresh))
    monkeypatch.setattr(tickets.secrets, "token_urlsafe", lambda _size: next(drawn))

    ticket, _ = await tickets.issue_download_ticket(uuid.uuid4(), uuid.uuid4(), expires_at=_LATER, now=_NOW)
    assert ticket == fresh and redis.store[tickets._key(taken)][0] == "occupied"


async def test_without_redis_there_is_no_ticket_and_no_download(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tickets, "get_redis", lambda: DownRedis())
    with pytest.raises(tickets.TicketUnavailable):
        await tickets.issue_download_ticket(uuid.uuid4(), uuid.uuid4(), expires_at=_LATER, now=_NOW)
    assert await tickets.redeem_download_ticket("t" * 43, uuid.uuid4()) is None


@pytest.mark.parametrize("stored", ["not json", json.dumps(["a"]), json.dumps({"job_id": "J", "user_id": "nope"})])
async def test_an_unreadable_payload_fails_closed(redis: FakeRedis, stored: str) -> None:
    job_id = uuid.UUID(int=7)
    ticket = "r" * 43
    redis.store[tickets._key(ticket)] = (stored.replace('"J"', f'"{job_id}"'), float("inf"), 60)
    assert await tickets.redeem_download_ticket(ticket, job_id) is None


# ── the ticket POST: the download's guard, refusals and bodies ──────────────

@pytest.mark.parametrize(
    ("who", "job", "expected"),
    [("admin", "ready", 200), ("lead", "ready", 200), ("outsider", "ready", 404), ("engineer", "ready", 404),
     ("viewer", "ready", 404), ("bound", "ready", 404), ("lead", "foreign", 404), ("admin", "mine", 409),
     ("admin", "theirs", 404), ("lead", "mine", 404), ("admin", "unknown", 404)],
)
async def test_the_ticket_answers_whom_the_download_answers(
    factory: Any, world: Any, storage: FakeStorage, redis: FakeRedis, who: str, job: str, expected: int,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    job_id = uuid.uuid4() if job == "unknown" else getattr(rows, job).id
    before = len(factory.statements)
    async with _client(factory, getattr(world, who)) as client:
        minted = await client.post(_ticket_path(job_id))
        download = await client.get(f"/api/v1/project-data-jobs/{job_id}/download")
    assert _writes(factory.statements[before:]) == [], "the ticket POST wrote SQL"
    assert minted.status_code == download.status_code == expected, minted.text
    if expected == 404:
        assert minted.json() == download.json() == NOT_FOUND
    if expected == 200:
        body = minted.json()
        assert set(body) == {"ticket", "expires_in", "url"} and body["expires_in"] == 60
        assert body["url"] == f"/api/v1/project-data-downloads/{job_id}?ticket={body['ticket']}"
        assert minted.headers["cache-control"] == "no-store"
        assert len(redis.store) == 1
    else:
        assert minted.json() == download.json() and redis.store == {}


@pytest.mark.parametrize(
    ("values", "status_code", "code"),
    [({"kind": "import", "status": "succeeded"}, 409, "not_an_export"),
     ({"kind": "export", "status": "queued"}, 409, "export_not_ready"),
     ({"kind": "export", "status": "running"}, 409, "export_not_ready"),
     ({"kind": "export", "status": "failed"}, 409, "export_failed"),
     ({"kind": "export", "status": "failed", "past": True}, 409, "export_failed"),
     ({"kind": "export", "status": "expired"}, 410, "export_expired"),
     ({"kind": "export", "status": "succeeded", "past": True}, 410, "export_expired"),
     ({"kind": "export", "status": "succeeded", "past": True, "no_key": True}, 410, "export_expired"),
     ({"kind": "export", "status": "succeeded", "no_key": True}, 502, "archive_unreadable")],
)
async def test_each_ticket_refusal_is_the_downloads_in_its_order(
    factory: Any, world: Any, storage: FakeStorage, redis: FakeRedis, values: dict, status_code: int, code: str,
) -> None:
    now = datetime.now(timezone.utc)
    expires = now - timedelta(minutes=1) if values.pop("past", False) else now + timedelta(hours=1)
    key = None if values.pop("no_key", False) else "project-exports/x/a.zip"
    job = await _job(factory, project_id=world.project.id, requested_by_id=world.admin.id, storage_key=key,
                     expires_at=expires, **values)
    before = len(factory.statements)
    async with _client(factory, world.admin) as client:
        minted = await client.post(_ticket_path(job.id))
        download = await client.get(f"/api/v1/project-data-jobs/{job.id}/download")
    assert minted.status_code == download.status_code == status_code
    assert minted.json() == download.json() == {"detail": {"code": code, "message": vocab.ERROR_SENTENCES[code]}}
    assert _writes(factory.statements[before:]) == [] and redis.store == {}


async def test_an_export_with_under_a_second_left_is_refused_as_expired(
    factory: Any, world: Any, redis: FakeRedis,
) -> None:
    job = await _job(factory, kind="export", status="succeeded", project_id=world.project.id,
                     requested_by_id=world.lead.id, storage_key="project-exports/x/a.zip",
                     expires_at=datetime.now(timezone.utc) + timedelta(milliseconds=600))
    async with _client(factory, world.lead) as client:
        minted = await client.post(_ticket_path(job.id))
    assert minted.status_code == 410
    assert minted.json()["detail"] == {"code": "export_expired", "message": vocab.ERROR_SENTENCES["export_expired"]}
    assert redis.store == {}


async def test_the_ticket_lives_no_longer_than_the_export(factory: Any, world: Any, redis: FakeRedis) -> None:
    job = await _job(factory, kind="export", status="succeeded", project_id=world.project.id,
                     requested_by_id=world.lead.id, storage_key="project-exports/x/a.zip",
                     expires_at=datetime.now(timezone.utc) + timedelta(seconds=30))
    async with _client(factory, world.lead) as client:
        minted = await client.post(_ticket_path(job.id))
    assert minted.status_code == 200
    (_, (_, _, ex)), = redis.store.items()
    assert minted.json()["expires_in"] == ex and 28 <= ex <= 30


async def test_without_redis_the_ticket_is_503_with_its_sentence(
    factory: Any, world: Any, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tickets, "get_redis", lambda: DownRedis())
    rows = await _jobs_of_every_kind(factory, world, storage)
    async with _client(factory, world.lead) as client:
        minted = await client.post(_ticket_path(rows.ready.id))
    assert minted.status_code == 503
    assert minted.json()["detail"] == {"code": "ticket_unavailable", "message": TICKET_SENTENCE}
    assert vocab.ERROR_SENTENCES["ticket_unavailable"] == TICKET_SENTENCE


# ── the ticket GET: the same bytes and headers, one 404 text, no SQL write ──

_SAME_HEADERS = ("content-type", "content-length", "content-disposition", "cache-control")


async def _mint(factory: Any, user: Any, job_id: Any) -> str:
    async with _client(factory, user) as client:
        minted = await client.post(_ticket_path(job_id))
    assert minted.status_code == 200, minted.text
    return str(minted.json()["url"])


def _assert_link_gone(answer: Any) -> None:
    assert answer.status_code == 404
    assert answer.text == LINK_GONE
    assert answer.headers["content-type"].startswith("text/plain")
    assert answer.headers["cache-control"] == "no-store" and answer.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("history", [False, True])
async def test_the_ticket_download_streams_what_the_bearer_download_streams(
    factory: Any, world: Any, storage: FakeStorage, redis: FakeRedis, history: bool,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    job = rows.ready
    if history:
        storage.objects["project-exports/x/history.zip"] = b"PK\x03\x04" + b"h" * 100
        job = await _job(factory, kind="export", status="succeeded", project_id=world.project.id, bytes=104,
                         requested_by_id=world.lead.id, storage_key="project-exports/x/history.zip",
                         finished_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
                         options={"include_history": True, "history_days": None, "max_runs": 2000})
    url = await _mint(factory, world.lead, job.id)
    before = len(factory.statements)
    async with _client(factory) as anonymous:
        answer = await anonymous.get(url)
    redeem_writes = _writes(factory.statements[before:])
    async with _client(factory, world.lead) as client:
        bearer_answer = await client.get(f"/api/v1/project-data-jobs/{job.id}/download")

    assert redeem_writes == [], "the ticket GET wrote SQL"
    assert answer.status_code == bearer_answer.status_code == 200, answer.text
    assert answer.content == bearer_answer.content == storage.objects[job.storage_key]
    assert {h: answer.headers[h] for h in _SAME_HEADERS} == {h: bearer_answer.headers[h] for h in _SAME_HEADERS}
    assert answer.headers["content-type"] == "application/zip" and answer.headers["cache-control"] == "no-store"
    assert answer.headers["content-length"] == str(job.bytes)
    kind = "project-data" if history else "settings-catalog"
    assert answer.headers["content-disposition"].startswith(f'attachment; filename="payment-service-{kind}-')
    assert answer.headers["referrer-policy"] == "no-referrer"
    assert redis.store == {}, "the ticket was spent"


async def test_a_spent_or_unknown_ticket_is_the_one_404_text(
    factory: Any, world: Any, storage: FakeStorage, redis: FakeRedis,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    url = await _mint(factory, world.lead, rows.ready.id)
    async with _client(factory) as anonymous:
        first = await anonymous.get(url)
        before = len(factory.statements)
        again = await anonymous.get(url)
        unknown = await anonymous.get(f"/api/v1/project-data-downloads/{rows.ready.id}?ticket={'u' * 43}")
    assert first.status_code == 200
    _assert_link_gone(again)
    _assert_link_gone(unknown)
    assert _writes(factory.statements[before:]) == []


async def test_job_as_ticket_does_not_open_job_b_and_is_spent(
    factory: Any, world: Any, storage: FakeStorage, redis: FakeRedis,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    second = await _job(factory, kind="export", status="succeeded", project_id=world.project.id, bytes=2504,
                        requested_by_id=world.lead.id, storage_key="project-exports/x/ready.zip",
                        finished_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    url = await _mint(factory, world.lead, rows.ready.id)
    async with _client(factory) as anonymous:
        aimed_at_b = await anonymous.get(url.replace(str(rows.ready.id), str(second.id)))
        then_a = await anonymous.get(url)
    _assert_link_gone(aimed_at_b)
    _assert_link_gone(then_a)


@pytest.mark.parametrize("change", ["deactivated", "removed_from_project", "demoted_to_engineer", "deleted"])
async def test_the_issuer_is_rechecked_at_the_download(
    factory: Any, world: Any, storage: FakeStorage, redis: FakeRedis, change: str,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    url = await _mint(factory, world.lead, rows.ready.id)
    async with factory() as db:
        lead = User.__table__.c
        if change == "deactivated":
            await db.execute(update(User).where(lead.id == world.lead.id).values(is_active=False))
        elif change == "removed_from_project":
            await db.execute(ProjectMember.__table__.delete().where(ProjectMember.user_id == world.lead.id))
        elif change == "demoted_to_engineer":
            await db.execute(update(User).where(lead.id == world.lead.id).values(role=UserRole.QA_ENGINEER.value))
        else:
            await db.execute(ProjectMember.__table__.delete().where(ProjectMember.user_id == world.lead.id))
            await db.execute(update(ProjectDataJob).values(requested_by_id=None))
            await db.execute(User.__table__.delete().where(lead.id == world.lead.id))
        await db.commit()
    before = len(factory.statements)
    async with _client(factory) as anonymous:
        answer = await anonymous.get(url)
    _assert_link_gone(answer)
    assert _writes(factory.statements[before:]) == []


@pytest.mark.parametrize(
    "change",
    [{"expires_at": "past"}, {"status": "expired"}, {"status": "expired", "storage_key": None},
     {"storage_key": None}, {"status": "failed"}],
)
async def test_the_export_is_rechecked_at_the_download(
    factory: Any, world: Any, storage: FakeStorage, redis: FakeRedis, change: dict,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    url = await _mint(factory, world.lead, rows.ready.id)
    if change.get("expires_at") == "past":
        change = {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    async with factory() as db:
        await db.execute(update(ProjectDataJob).where(ProjectDataJob.id == rows.ready.id).values(**change))
        await db.commit()
    before = len(factory.statements)
    async with _client(factory) as anonymous:
        answer = await anonymous.get(url)
    _assert_link_gone(answer)
    assert _writes(factory.statements[before:]) == []


async def test_a_malformed_link_is_the_same_404_and_spends_no_ticket(
    factory: Any, world: Any, storage: FakeStorage, redis: FakeRedis,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    url = await _mint(factory, world.lead, rows.ready.id)
    ticket = url.split("ticket=", 1)[1]
    base = f"/api/v1/project-data-downloads/{rows.ready.id}"
    async with _client(factory) as anonymous:
        refused = [
            await anonymous.get(base),                                              # no ticket
            await anonymous.get(f"{base}?ticket=short"),                            # under 16
            await anonymous.get(f"{base}?ticket={'x' * 129}"),                      # over 128
            await anonymous.get(f"/api/v1/project-data-downloads/not-a-uuid?ticket={ticket}"),
        ]
        assert redis.getdels == [], "a malformed link reached the ticket store"
        still_live = await anonymous.get(url)
    for answer in refused:
        _assert_link_gone(answer)
    assert still_live.status_code == 200


async def test_without_redis_the_download_fails_closed(
    factory: Any, world: Any, storage: FakeStorage, redis: FakeRedis, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    url = await _mint(factory, world.lead, rows.ready.id)
    monkeypatch.setattr(tickets, "get_redis", lambda: DownRedis())
    async with _client(factory) as anonymous:
        answer = await anonymous.get(url)
    _assert_link_gone(answer)


async def test_a_storage_fault_before_the_first_byte_is_a_502_text(
    factory: Any, world: Any, storage: FakeStorage, redis: FakeRedis,
) -> None:
    rows = await _jobs_of_every_kind(factory, world, storage)
    url = await _mint(factory, world.lead, rows.ready.id)
    storage.fail_stream = True
    async with _client(factory) as anonymous:
        answer = await anonymous.get(url)
    assert answer.status_code == 502 and answer.text == vocab.ERROR_SENTENCES["archive_unreadable"]
    assert answer.headers["content-type"].startswith("text/plain")


# ── pins: the public router, its one route, its credential ──────────────────

def test_the_ticket_download_is_public_and_the_ticket_post_is_protected() -> None:
    """A navigation cannot send Authorization: the GET must not sit behind the router-wide header auth."""
    from fastapi.routing import APIRoute

    from app.bootstrap import PROTECTED_ROUTERS, PUBLIC_ROUTERS

    assert routes.public_router in PUBLIC_ROUTERS and routes.public_router not in PROTECTED_ROUTERS
    assert routes.router in PROTECTED_ROUTERS and routes.router not in PUBLIC_ROUTERS
    public = [route for route in routes.public_router.routes if isinstance(route, APIRoute)]
    assert [(route.path, route.methods) for route in public] == [
        ("/api/v1/project-data-downloads/{data_job_id}", {"GET"}),
    ]
    names = {getattr(dependency.call, "__qualname__", "") for dependency in public[0].dependant.dependencies}
    assert any("require_project_data_download_ticket" in name for name in names)
    assert not any("get_current_user_or_api_key" in name or "get_current_active_user" in name for name in names)
    protected = [(route.path, route.methods) for route in routes.router.routes if isinstance(route, APIRoute)]
    assert protected == [("/api/v1/project-data-jobs/{data_job_id}/download-ticket", {"POST"})]
    guard = inspect.signature(routes.issue_project_export_download_ticket).parameters["job"].default
    assert "require_project_data_job_access" in guard.dependency.__qualname__


async def test_the_mounted_app_answers_a_header_less_navigation_with_the_404_text(redis: FakeRedis) -> None:
    """The real app (middleware, PUBLIC_ROUTERS): no 401 for a navigation without Authorization."""
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        answer = await client.get(f"/api/v1/project-data-downloads/{uuid.uuid4()}", params={"ticket": "n" * 43})
        minted = await client.post(_ticket_path(uuid.uuid4()))
    _assert_link_gone(answer)
    assert len(redis.getdels) == 1
    assert minted.status_code == 401, "the ticket POST needs a credential"
