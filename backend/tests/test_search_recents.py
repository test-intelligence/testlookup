"""W12 SRCH-03: recent searches follow the user (Redis, per user, 20, 30 days).

* ``push_recent`` dedupes on the normalised query + scope + mode + project,
  keeps the newest 20, refreshes a 30-day TTL, and keys per user;
* ``GET /search/recent`` FILTERS an entry of a project the reader lost (a GET
  never writes: no command but ``LRANGE``) and keeps All-Projects entries;
* a Redis fault: the GET answers ``items: []``, the DELETE 503s with its
  sentence, a search never fails;
* only a page-1 search the /search page asked to record (``record=true``)
  pushes an entry -- the typeahead's call shape (``size=5``, no ``record``),
  pagination and every other caller push none;
* the query text never reaches a log line (``zq-sentinel-7`` across all of it).

Over a recording async Redis fake.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.search_vocab import RECENT_MAX, RECENT_TTL_DAYS
from app.services import search_recents

SENTINEL = "zq-sentinel-7"
USER_A, USER_B = uuid.uuid4(), uuid.uuid4()
KEPT, LOST = uuid.uuid4(), uuid.uuid4()


class _Pipe:
    def __init__(self, redis: "FakeRedis", transaction: bool) -> None:
        self._redis, self.transaction, self._ops = redis, transaction, []

    def delete(self, key):
        self._ops.append(("DEL", key))
        return self

    def rpush(self, key, *values):
        self._ops.append(("RPUSH", key, values))
        return self

    def expire(self, key, seconds):
        self._ops.append(("EXPIRE", key, seconds))
        return self

    async def execute(self):
        self._redis.commands.append(("EXEC", self.transaction, tuple(op[0] for op in self._ops)))
        for op in self._ops:
            if op[0] == "DEL":
                self._redis.data.pop(op[1], None)
            elif op[0] == "RPUSH":
                self._redis.data.setdefault(op[1], []).extend(op[2])
            else:
                self._redis.ttl[op[1]] = op[2]
        return [True] * len(self._ops)


class FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.data: dict[str, list[str]] = {}
        self.ttl: dict[str, int] = {}
        self.commands: list[tuple] = []
        self.fail = fail

    def _maybe_fail(self):
        if self.fail:
            # The message carries the query on purpose: only the TYPE may be logged.
            raise ConnectionError(f"redis unreachable while handling {SENTINEL}")

    async def lrange(self, key, start, stop):
        self.commands.append(("LRANGE", key, start, stop))
        self._maybe_fail()
        return list(self.data.get(key, []))[start:stop + 1]

    async def delete(self, key):
        self.commands.append(("DEL", key))
        self._maybe_fail()
        self.data.pop(key, None)

    def pipeline(self, transaction: bool = True):
        self.commands.append(("PIPELINE", transaction))
        self._maybe_fail()
        return _Pipe(self, transaction)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(search_recents, "get_redis", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _capture(caplog, capsys):
    """Every log line of every test here: stdlib records AND structlog's own output."""
    caplog.set_level(logging.DEBUG)
    yield
    logged = "\n".join(f"{r.getMessage()} {r.args!r}" for r in caplog.records)
    out, err = capsys.readouterr()
    assert SENTINEL not in logged + out + err, "the query text reached a log line"


def _entry(q=f"checkout {SENTINEL}", *, scope="all", mode="keyword", project=None, count=3, ts=None):
    return {
        "q": q, "scope": scope, "mode": mode, "project_id": str(project) if project else None,
        "result_count": count, "counts_exact": True,
        "ts": (ts or datetime(2026, 10, 24, 12, tzinfo=timezone.utc)).isoformat(),
    }


def _stored(redis: FakeRedis, user) -> list[dict]:
    return [json.loads(raw) for raw in redis.data.get(f"search:recent:{user}", [])]


# ── 1. push_recent ───────────────────────────────────────────────────────────


async def test_push_dedupes_on_the_normalised_query_scope_mode_and_project(redis):
    assert await search_recents.push_recent(str(USER_A), _entry(f"Checkout  {SENTINEL}", count=1))
    assert await search_recents.push_recent(str(USER_A), _entry("other", count=2))
    assert await search_recents.push_recent(str(USER_A), _entry(f" checkout {SENTINEL} ", count=9))
    assert await search_recents.push_recent(str(USER_A), _entry(f"checkout {SENTINEL}", scope="tests"))
    assert await search_recents.push_recent(str(USER_A), _entry(f"checkout {SENTINEL}", project=KEPT))
    stored = _stored(redis, USER_A)
    assert [(e["q"], e["scope"], e["project_id"], e["result_count"]) for e in stored] == [
        (f"checkout {SENTINEL}", "all", str(KEPT), 3),
        (f"checkout {SENTINEL}", "tests", None, 3),
        (f"checkout {SENTINEL}", "all", None, 9),  # the repeat moved up, newest values kept
        ("other", "all", None, 2),
    ]


async def test_push_keeps_the_newest_twenty_and_refreshes_a_thirty_day_ttl(redis):
    for index in range(RECENT_MAX + 5):
        await search_recents.push_recent(str(USER_A), _entry(f"q{index}"))
    stored = _stored(redis, USER_A)
    assert len(stored) == RECENT_MAX == 20
    assert stored[0]["q"] == "q24" and stored[-1]["q"] == "q5"
    assert redis.ttl[f"search:recent:{USER_A}"] == RECENT_TTL_DAYS * 86400 == 2_592_000
    # Each write is ONE transactional pipeline: DEL, RPUSH, EXPIRE.
    assert redis.commands[-1] == ("EXEC", True, ("DEL", "RPUSH", "EXPIRE"))
    lranges = [c for c in redis.commands if c[0] == "LRANGE"]
    assert all(c[2:] == (0, RECENT_MAX - 1) for c in lranges)


async def test_entries_are_kept_per_user(redis):
    await search_recents.push_recent(str(USER_A), _entry(f"a {SENTINEL}"))
    await search_recents.push_recent(str(USER_B), _entry("b only"))
    assert [e["q"] for e in await search_recents.read_recents(str(USER_B))] == ["b only"]
    assert [e["q"] for e in await search_recents.read_recents(str(USER_A))] == [f"a {SENTINEL}"]


async def test_a_malformed_entry_is_never_written_and_a_malformed_stored_one_is_skipped(redis):
    assert await search_recents.push_recent(str(USER_A), _entry(scope="everything")) is False
    assert await search_recents.push_recent(str(USER_A), _entry(q="x" * 501)) is False
    redis.data[f"search:recent:{USER_A}"] = ["not json", json.dumps({"q": 1}), json.dumps(_entry("ok"))]
    assert [e["q"] for e in await search_recents.read_recents(str(USER_A))] == ["ok"]


# ── 2. GET /search/recent filters on read and writes nothing ────────────────


async def test_the_get_drops_a_lost_project_keeps_all_projects_and_only_reads(redis, monkeypatch):
    from app.routers import search as router

    key = f"search:recent:{USER_A}"
    redis.data[key] = [
        json.dumps(_entry("kept", project=KEPT)),
        json.dumps(_entry(f"lost {SENTINEL}", project=LOST)),
        json.dumps(_entry("everywhere")),
    ]
    monkeypatch.setattr(router, "get_accessible_project_ids", AsyncMock(return_value={KEPT}))
    redis.commands.clear()

    body = await router.list_recent_searches(db=MagicMock(), current_user=MagicMock(id=USER_A))

    assert [e["q"] for e in body["items"]] == ["kept", "everywhere"]
    assert body["max"] == 20 and body["retention_days"] == 30
    assert [c[0] for c in redis.commands] == ["LRANGE"], "a GET of recents wrote to Redis"
    assert len(redis.data[key]) == 3, "the lost project's entry was deleted, not filtered"


async def test_an_admin_keeps_every_entry(redis, monkeypatch):
    from app.routers import search as router

    redis.data[f"search:recent:{USER_A}"] = [json.dumps(_entry("x", project=LOST))]
    monkeypatch.setattr(router, "get_accessible_project_ids", AsyncMock(return_value=None))
    body = await router.list_recent_searches(db=MagicMock(), current_user=MagicMock(id=USER_A))
    assert [e["q"] for e in body["items"]] == ["x"]


async def test_the_response_model_reads_the_stored_shape(redis, monkeypatch):
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import search as router

    redis.data[f"search:recent:{USER_A}"] = [json.dumps(_entry("payment", scope="tests", mode="semantic", project=KEPT))]
    monkeypatch.setattr(router, "get_accessible_project_ids", AsyncMock(return_value={KEPT}))
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_current_active_user] = lambda: MagicMock(id=USER_A)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/api/v1/search/recent")
    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["q"] == "payment" and item["project_id"] == str(KEPT) and item["mode"] == "semantic"
    assert datetime.fromisoformat(item["ts"].replace("Z", "+00:00")) == datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


# ── 3. A Redis fault ─────────────────────────────────────────────────────────


async def test_a_redis_fault_answers_nothing_stored_and_the_delete_503s(monkeypatch):
    from app.routers import search as router

    fake = FakeRedis(fail=True)
    monkeypatch.setattr(search_recents, "get_redis", lambda: fake)
    body = await router.list_recent_searches(db=MagicMock(), current_user=MagicMock(id=USER_A))
    assert body["items"] == []
    assert await search_recents.push_recent(str(USER_A), _entry()) is False
    with pytest.raises(HTTPException) as raised:
        await router.clear_recent_searches(current_user=MagicMock(id=USER_A))
    assert raised.value.status_code == 503
    assert raised.value.detail == "Could not clear recent searches: the cache is unavailable."


async def test_the_delete_clears_only_the_callers_list(redis):
    from app.routers import search as router

    await search_recents.push_recent(str(USER_A), _entry("a"))
    await search_recents.push_recent(str(USER_B), _entry("b"))
    assert await router.clear_recent_searches(current_user=MagicMock(id=USER_A)) is None
    assert f"search:recent:{USER_A}" not in redis.data
    assert _stored(redis, USER_B)[0]["q"] == "b"


# ── 4. Only a recorded page-1 search pushes an entry ────────────────────────


class _UsageSession:
    added: list = []

    def add(self, row):
        _UsageSession.added.append(row)

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


@pytest.fixture
def app_client(redis, monkeypatch):
    import app.db.postgres as pg
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import search as router

    _UsageSession.added = []
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _UsageSession(), raising=False)
    monkeypatch.setattr(router, "resolve_project_scope", AsyncMock(return_value=(KEPT, None)))
    monkeypatch.setattr(
        "app.services.global_search_service.global_search",
        AsyncMock(return_value={"items": [], "total": 4, "counts_are_exact": True, "query": "q"}),
    )
    app = FastAPI()
    app.include_router(router.router)

    async def _db():
        yield MagicMock()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: MagicMock(id=USER_A)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.parametrize(
    ("params", "pushed"),
    [
        ({"record": "true"}, 1),
        ({}, 0),                                    # every other caller: no record
        ({"size": "5"}, 0),                         # the typeahead's call shape
        ({"record": "true", "page": "2"}, 0),       # pagination never records
    ],
    ids=["recorded", "not_recorded", "typeahead", "page_2"],
)
async def test_only_a_recorded_page_one_search_pushes_an_entry(app_client, redis, params, pushed):
    async with app_client as client:
        response = await client.get(
            "/api/v1/search/global", params={"q": f"checkout {SENTINEL}", "project_id": str(KEPT), **params},
        )
    assert response.status_code == 200, response.text
    stored = _stored(redis, USER_A)
    assert len(stored) == pushed
    if pushed:
        assert stored[0] == {
            "q": f"checkout {SENTINEL}", "scope": "all", "mode": "keyword", "project_id": str(KEPT),
            "result_count": 4, "counts_exact": True, "ts": stored[0]["ts"],
        }
        assert abs(datetime.fromisoformat(stored[0]["ts"]) - datetime.now(timezone.utc)) < timedelta(minutes=1)


async def test_an_empty_query_is_never_recorded(app_client, redis):
    async with app_client as client:
        response = await client.get("/api/v1/search/global", params={"q": "  ", "record": "true"})
    assert response.status_code == 200
    assert _stored(redis, USER_A) == []


async def test_a_one_type_search_records_its_scope(app_client, redis):
    async with app_client as client:
        await client.get(
            "/api/v1/search/global", params={"q": "payment", "entity_types": "defect", "record": "true"},
        )
    assert _stored(redis, USER_A)[0]["scope"] == "defects"
