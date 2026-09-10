"""Both dead-letter stores are readable -- by an admin -- and an outage is not "empty".

Re-audit M2. Work that permanently failed lands in one of two Redis stores:
``persist_live_session`` runs out of retries into a LIST, while live events the
consumer gave up on (then ACKed away, so the entry is the only copy) and
Celery tasks out of retries go to a STREAM. ``/health/ingestion`` counted the
list, nothing counted the stream, and no API returned an entry of either.

These write through the three production writers into a fake Redis, then read
back through the real app over HTTP.
"""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")

from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio

URL = "/api/v1/admin/maintenance/dlq"


class _Pipe:
    def __init__(self, redis: "_Redis") -> None:
        self.redis = redis
        self.ops: list[tuple] = []

    def lpush(self, key, value):
        self.ops.append(("lpush", key, value))
        return self

    def ltrim(self, key, start, end):
        self.ops.append(("ltrim", key, start, end))
        return self

    def expire(self, _key, _seconds):
        return self

    async def execute(self):
        for op in self.ops:
            if op[0] == "lpush":
                self.redis.lists.setdefault(op[1], []).insert(0, op[2])
            else:
                _, key, start, end = op
                self.redis.lists[key] = self.redis.lists.get(key, [])[start:end + 1]
        self.ops.clear()


class _Redis:
    """The LIST and STREAM commands the DLQ writers and readers use."""

    def __init__(self) -> None:
        self.lists: dict[str, list] = {}
        self.streams: dict[str, list] = {}
        self._ids = itertools.count(1)

    def pipeline(self, transaction=True):
        return _Pipe(self)

    async def lrange(self, key, start, end):
        return self.lists.get(key, [])[start:end + 1]

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def xadd(self, name, fields, maxlen=None, approximate=True):
        msg_id = f"{1725960000000 + next(self._ids)}-0"
        self.streams.setdefault(name, []).append((msg_id, dict(fields)))
        return msg_id

    async def xrange(self, name, count=None, **_kwargs):
        entries = list(self.streams.get(name, []))
        return entries[:count] if count else entries

    async def xrevrange(self, name, count=None, **_kwargs):
        entries = list(reversed(self.streams.get(name, [])))
        return entries[:count] if count else entries

    async def xlen(self, name):
        return len(self.streams.get(name, []))


class _DownRedis:
    def pipeline(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def lrange(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def llen(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def xrange(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def xrevrange(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")

    async def xlen(self, *_args, **_kwargs):
        raise ConnectionError("redis is down")


def _use(monkeypatch, fake) -> None:
    for target in ("app.db.redis_client.get_redis", "app.streams.live_consumer.get_redis"):
        monkeypatch.setattr(target, lambda: fake)


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    _use(monkeypatch, fake)
    return fake


async def _fill() -> None:
    """One failure from each production writer, oldest first."""
    from app.services.ingestion_dlq import record_persist_failure
    from app.streams.live_consumer import LiveEventStreamConsumer
    from app.worker.tasks import _send_to_dlq

    await record_persist_failure(
        run_id="run-1", project_id="p-1", task_id="t-1", retry_count=3, error="db down"
    )
    await LiveEventStreamConsumer()._move_to_dlq(
        "5-0", {"type": "test_result", "test_name": "checkout"}, "boom", 3
    )
    await _send_to_dlq("run_agent_pipeline", "t-2", {"test_run_id": "run-2"}, "llm timeout")


# ── The admin route ──────────────────────────────────────────────────────


async def test_an_admin_reads_both_stores(client, auth_as, redis):
    await _fill()
    auth_as(role=UserRole.ADMIN)

    resp = await client.get(URL)

    assert resp.status_code == 200, resp.text
    sources = resp.json()["sources"]

    persist = sources["persist_live_session"]
    assert persist["count"] == 1
    assert persist["entries"][0]["run_id"] == "run-1"

    stream = sources["stream"]
    assert stream["count"] == 2, "the stream DLQ is still unread"
    newest, oldest = stream["entries"]
    assert newest["source"] == "celery", "entries are not newest-first"
    assert newest["task_name"] == "run_agent_pipeline"
    assert newest["kwargs"]["test_run_id"] == "run-2"
    assert oldest["original_data"]["test_name"] == "checkout", (
        "the only surviving copy of a failed live event came back undecoded"
    )
    assert all("failed_at" in entry for entry in stream["entries"])


@pytest.mark.parametrize(
    "role", [UserRole.VIEWER, UserRole.TESTER, UserRole.QA_ENGINEER, UserRole.QA_LEAD]
)
async def test_only_an_instance_admin_can_read_dead_letters(client, auth_as, redis, role):
    """Entries are cross-tenant: run ids, project ids, error text, event payloads."""
    await _fill()
    auth_as(role=role)

    resp = await client.get(URL)

    assert resp.status_code == 403, resp.text


async def test_no_credentials_are_refused(client, redis):
    resp = await client.get(URL)
    assert resp.status_code == 401, resp.text


async def test_an_unreadable_store_is_a_503_not_an_empty_list(client, auth_as, monkeypatch):
    _use(monkeypatch, _DownRedis())
    auth_as(role=UserRole.ADMIN)

    resp = await client.get(URL)

    assert resp.status_code == 503, (
        "a Redis outage came back as a dead-letter queue with nothing in it: " + resp.text
    )


# ── Each reader, on its own ──────────────────────────────────────────────


async def test_the_list_reader_raises_rather_than_returning_empty(monkeypatch):
    """It returned ``[]`` on any error: an outage read as "no failures"."""
    from app.services.ingestion_dlq import DLQUnavailable, list_recent_failures

    _use(monkeypatch, _DownRedis())
    with pytest.raises(DLQUnavailable):
        await list_recent_failures()


async def test_the_stream_reader_raises_rather_than_returning_empty(monkeypatch):
    from app.services.ingestion_dlq import DLQUnavailable, list_recent_stream_failures

    _use(monkeypatch, _DownRedis())
    with pytest.raises(DLQUnavailable):
        await list_recent_stream_failures()


async def test_a_malformed_json_field_is_kept_as_text(redis):
    """This may be the only copy of the event, so an entry is never dropped."""
    from app.services.ingestion_dlq import list_recent_stream_failures
    from app.streams import DLQ_STREAM

    await redis.xadd(DLQ_STREAM, {"source": "celery", "task_name": "x", "kwargs": "{not json"})

    [entry] = await list_recent_stream_failures()

    assert entry["kwargs"] == "{not json"


# ── /health/ingestion counts both ────────────────────────────────────────


async def test_health_counts_the_stream_dlq_too(client, redis):
    await _fill()

    resp = await client.get("/health/ingestion")

    dlq = resp.json()["dlq"]
    assert dlq["persist_live_session"] == 1
    assert dlq["stream"] == 2, "/health/ingestion still counts only one of the two stores"


async def test_health_reports_an_unreadable_stream_count_as_unknown(client, monkeypatch):
    _use(monkeypatch, _DownRedis())

    resp = await client.get("/health/ingestion")

    assert resp.json()["dlq"]["stream"] is None


# ── A key bound to one project is not an instance admin (QA of M2) ──────


async def test_an_admins_project_bound_key_cannot_read_dead_letters(client, auth_as, redis):
    """QA's probe. The entries span tenants: a key an admin minted for one
    team's CI must not read every tenant's run ids, error text and task
    arguments."""
    import uuid

    await _fill()
    auth_as(role=UserRole.ADMIN, bound_project_id=uuid.uuid4())
    resp = await client.get(URL)
    assert resp.status_code == 403, resp.text
    assert "run-1" not in resp.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/maintenance/backfill-placeholder-test-cases",
        "/api/v1/admin/maintenance/backfill-unassigned-failures",
        "/api/v1/admin/maintenance/drain-active-live-sessions",
    ],
)
async def test_a_project_bound_key_cannot_start_work_that_walks_every_project(client, auth_as, path):
    import uuid

    auth_as(role=UserRole.ADMIN, bound_project_id=uuid.uuid4())
    resp = await client.post(path)
    assert resp.status_code == 403, resp.text


async def test_every_maintenance_route_requires_an_instance_admin():
    """A route added to this router later must not fall back to require_role."""
    import ast
    import pathlib

    import app.routers.admin_maintenance as module

    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    routes = [
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(dec, ast.Call)
            and getattr(dec.func, "attr", "") in {"get", "post", "put", "patch", "delete"}
            for dec in node.decorator_list
        )
    ]
    assert len(routes) >= 4, [route.name for route in routes]
    for route in routes:
        gates = " ".join(
            ast.unparse(kw.value)
            for dec in route.decorator_list if isinstance(dec, ast.Call)
            for kw in dec.keywords if kw.arg == "dependencies"
        )
        assert "require_instance_admin()" in gates, (
            f"{route.name} is not gated by require_instance_admin: {gates or 'no dependencies'}"
        )
