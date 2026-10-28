"""The activity live-tail stream route and its frames (W21d ACT-02 §2.2).

Refusals go through a real app (the router's real guard chain over a scripted
session); the frame sequences drive ``live.stream_frames`` with
``read_after`` / ``stream_head`` scripted and the module's clock and sleep
patched (``live._clock`` / ``live._sleep``, never the global
``asyncio.sleep``), times built inside each test. Streaming bodies are read
from the handler's own ``StreamingResponse.body_iterator``: httpx's
``ASGITransport`` buffers a streaming body until it ends.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core import deps
from app.models.postgres import UserRole
from app.routers import activity as activity_router
from app.services.activity import live

STREAM = "/api/v1/projects/{pid}/activity/stream"


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    """The request session: answers the guard's membership read, records close."""

    def __init__(self, member: bool = True) -> None:
        self.member = member
        self.closed = 0
        self.new: list = []
        self.dirty: list = []
        self.deleted: list = []

    async def execute(self, stmt):
        return _Result(uuid.uuid4() if self.member else None)

    async def close(self):
        self.closed += 1


def _user(kind: str = deps.CREDENTIAL_KIND_JWT, role: UserRole = UserRole.QA_ENGINEER, bound=None):
    user = SimpleNamespace(
        id=uuid.uuid4(), role=role, is_active=True, must_change_password=False,
        username="ana", full_name="Ana", is_service_account=False,
    )
    deps._bind_api_key_project(user, bound)
    deps._bind_api_key_grant(user, None)
    return deps._bind_credential_kind(user, kind)


def _app(user, session: _Session) -> FastAPI:
    app = FastAPI()
    app.include_router(activity_router.router)

    async def _db():
        yield session

    app.dependency_overrides[deps.get_db] = _db
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    return app


async def _get(app: FastAPI, path: str, headers: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers or {})


def _request(headers: dict | None = None, disconnected: list | None = None):
    flags = disconnected if disconnected is not None else [False]

    async def _is_disconnected() -> bool:
        return flags[0]

    return SimpleNamespace(headers=headers or {}, is_disconnected=_is_disconnected)


@pytest.fixture(autouse=True)
def _fresh_slots():
    live.SLOTS.clear()
    yield
    live.SLOTS.clear()


@pytest.fixture
def clock(monkeypatch):
    """A fake monotonic clock that only ``live._sleep`` advances."""
    now = [10_000.0]

    async def _sleep(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(live, "_clock", lambda: now[0])
    monkeypatch.setattr(live, "_sleep", _sleep)
    return now


class _Redis:
    """``stream_head`` / ``read_after`` scripted per call."""

    def __init__(self, head: str = "0-0") -> None:
        self.head = head
        self.polls: list[Any] = []  # each: list of (id, payload) | "gap" | Exception
        self.resume: Any = None  # the answer to a resume's first read, once
        self.read_calls: list[str] = []

    async def stream_head(self, project_id):
        return self.head

    async def read_after(self, project_id, after_id, *, limit=live.REPLAY_LIMIT):
        self.read_calls.append(after_id)
        if self.resume is not None:
            answer, self.resume = self.resume, None
            return answer
        item = self.polls.pop(0) if self.polls else []
        if isinstance(item, Exception):
            raise item
        if item == "gap":
            return [], True
        return [(eid, {"payload": data}) for eid, data in item], False


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr(live, "stream_head", fake.stream_head)
    monkeypatch.setattr(live, "read_after", fake.read_after)
    return fake


async def _frames(gen) -> list[str]:
    return [chunk async for chunk in gen]


def _parse(chunks: list[str]) -> list[tuple]:
    """Each chunk → ("retry", n) | (event, id, data) | ("heartbeat",)."""
    out: list[tuple] = []
    for chunk in chunks:
        if chunk.startswith("retry:"):
            out.append(("retry", int(chunk.split(":")[1])))
            continue
        if chunk.startswith(": heartbeat"):
            out.append(("heartbeat",))
            continue
        fields = dict(line.split(": ", 1) for line in chunk.strip().split("\n"))
        out.append((fields["event"], fields.get("id"), fields["data"]))
    return out


def _gen(project_id, *, head="0-0", last=None, lifetime=5, reason="max_age", flags=None, closed=None):
    closed = closed if closed is not None else []
    return live.stream_frames(
        project_id=project_id,
        head=head,
        last_event_id=last,
        lifetime_seconds=lifetime,
        deadline_reason=reason,
        is_disconnected=_request(disconnected=flags).is_disconnected,
        on_close=lambda: closed.append(True),
    )


# ── Frame sequences ──────────────────────────────────────────────────────────


async def test_no_header_ready_at_the_head_then_rows_oldest_first_then_bye_max_age(clock, redis):
    pid = uuid.uuid4()
    redis.head = "1730000000000-0"
    row_1, row_2 = '{"id":"r1"}', '{"id":"r2"}'
    redis.polls = [[("1730000000001-0", row_1), ("1730000000002-0", row_2)]]
    closed: list = []
    frames = _parse(await _frames(_gen(pid, head=redis.head, lifetime=3, closed=closed)))

    assert frames[0] == ("retry", 5000)
    event, frame_id, data = frames[1]
    assert (event, frame_id) == ("ready", "1730000000000-0")
    assert json.loads(data) == {
        "project_id": str(pid),
        "last_event_id": "1730000000000-0",
        "heartbeat_seconds": 15,
        "max_seconds": 3,
    }
    # The stored payload string verbatim: no re-serialization.
    assert frames[2:4] == [
        ("activity", "1730000000001-0", row_1),
        ("activity", "1730000000002-0", row_2),
    ]
    assert frames[-1] == ("bye", None, '{"reason":"max_age"}')
    assert redis.read_calls[:2] == ["1730000000000-0", "1730000000002-0"], "the cursor moves"
    assert closed == [True]


async def test_a_clean_replay_resumes_from_the_clients_id(clock, redis):
    pid = uuid.uuid4()
    redis.head = "1730000000009-0"
    redis.resume = ([("1730000000005-0", {"payload": '{"id":"missed"}'})], False)
    frames = _parse(await _frames(_gen(pid, head=redis.head, last="1730000000004-0", lifetime=2)))

    assert frames[1][:2] == ("ready", "1730000000004-0")
    assert json.loads(frames[1][2])["last_event_id"] == "1730000000004-0"
    assert frames[2] == ("activity", "1730000000005-0", '{"id":"missed"}')
    assert redis.read_calls == ["1730000000004-0", "1730000000005-0"]


async def test_a_gap_is_ready_at_the_head_and_refresh_gap(clock, redis):
    pid = uuid.uuid4()
    redis.head = "1730000000009-0"
    redis.resume = ([], True)
    frames = _parse(await _frames(_gen(pid, head=redis.head, last="1730000000001-0", lifetime=2)))

    assert frames[1][:2] == ("ready", "1730000000009-0")
    assert frames[2] == ("refresh", "1730000000009-0", '{"reason":"gap"}')
    assert redis.read_calls[1] == "1730000000009-0", "polling continues from the head"


async def test_an_id_ahead_of_the_head_is_a_gap_too(clock, redis):
    """Not an id this stream issued: never wait for the stream to catch up with it."""
    redis.head = "1730000000009-0"
    frames = _parse(await _frames(_gen(uuid.uuid4(), head=redis.head, last="99999999999999-0", lifetime=1)))
    assert frames[2] == ("refresh", "1730000000009-0", '{"reason":"gap"}')


async def test_an_overflow_mid_stream_is_refresh_overflow_from_the_newest(clock, redis):
    pid = uuid.uuid4()
    redis.head = "1730000000000-0"
    redis.polls = ["gap"]
    gen = _gen(pid, head=redis.head, lifetime=3)
    redis.head = "1730000000500-0"  # by the time of the poll, 500 rows later
    frames = _parse(await _frames(gen))
    assert ("refresh", "1730000000500-0", '{"reason":"overflow"}') in frames
    assert redis.read_calls[1] == "1730000000500-0", "continues from the newest"


async def test_the_deadline_says_token_expired_when_the_token_ends_first(clock, redis):
    frames = _parse(await _frames(_gen(uuid.uuid4(), lifetime=2, reason="token_expired")))
    assert frames[-1] == ("bye", None, '{"reason":"token_expired"}')
    assert json.loads(frames[1][2])["max_seconds"] == 2


async def test_redis_failing_mid_stream_is_bye_unavailable(clock, redis):
    redis.polls = [[], live.LiveUnavailable("ConnectionError")]
    closed: list = []
    frames = _parse(await _frames(_gen(uuid.uuid4(), lifetime=60, closed=closed)))
    assert frames[-1] == ("bye", None, '{"reason":"unavailable"}')
    assert closed == [True]


async def test_redis_failing_on_resume_is_bye_unavailable(clock, redis, monkeypatch):
    async def _down(*args, **kwargs):
        raise live.LiveUnavailable("ConnectionError")

    monkeypatch.setattr(live, "read_after", _down)
    frames = _parse(await _frames(_gen(uuid.uuid4(), head="5-0", last="4-0", lifetime=60)))
    assert [f[0] for f in frames] == ["retry", "ready", "bye"]
    assert frames[-1][2] == '{"reason":"unavailable"}'


async def test_a_heartbeat_after_15_seconds_without_a_frame(clock, redis):
    frames = _parse(await _frames(_gen(uuid.uuid4(), lifetime=20)))
    kinds = [f[0] for f in frames]
    assert kinds.count("heartbeat") == 1
    # 15 one-second polls of silence, then the heartbeat, then the bye at 20 s.
    assert kinds == ["retry", "ready", "heartbeat", "bye"]


async def test_a_disconnect_stops_the_stream_and_releases(clock, redis):
    flags = [False]
    closed: list = []
    gen = _gen(uuid.uuid4(), lifetime=600, flags=flags, closed=closed)
    assert (await gen.__anext__()).startswith("retry")
    assert (await gen.__anext__()).startswith("event: ready")
    flags[0] = True
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert closed == [True]


# ── The route: refusals in order, caps, the release, the registration ──────


async def test_a_non_member_gets_the_guards_own_403_first(redis):
    app = _app(_user(), _Session(member=False))
    response = await _get(app, STREAM.format(pid=uuid.uuid4()), {"Last-Event-ID": "bad"})
    assert response.status_code == 403
    assert response.json() == {"detail": "You do not have access to this project"}


async def test_an_api_key_is_refused_before_the_last_event_id_is_judged(redis):
    pid = uuid.uuid4()
    key_user = _user(kind=deps.CREDENTIAL_KIND_API_KEY, bound=pid)
    response = await _get(_app(key_user, _Session()), STREAM.format(pid=pid), {"Last-Event-ID": "bad"})
    assert response.status_code == 403
    assert response.json() == {"detail": {
        "code": "interactive_login_required",
        "message": _sentence("interactive_login_required"),
    }}


async def test_a_key_bound_to_another_project_gets_the_guards_403(redis):
    key_user = _user(kind=deps.CREDENTIAL_KIND_API_KEY, bound=uuid.uuid4())
    response = await _get(_app(key_user, _Session()), STREAM.format(pid=uuid.uuid4()))
    assert response.status_code == 403
    assert isinstance(response.json()["detail"], str), "the guard's string, before the handler"


@pytest.mark.parametrize("header", ["bad", "1730000000000", "1-2-3", "-1-0", "1" * 21 + "-0"])
async def test_a_malformed_last_event_id_is_400(redis, header):
    response = await _get(_app(_user(), _Session()), STREAM.format(pid=uuid.uuid4()), {"Last-Event-ID": header})
    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "invalid_last_event_id",
        "message": _sentence("invalid_last_event_id"),
    }


async def test_redis_unavailable_at_connect_is_503_live_unavailable(monkeypatch):
    async def _down(project_id):
        raise live.LiveUnavailable("ConnectionError")

    monkeypatch.setattr(live, "stream_head", _down)
    session = _Session()
    response = await _get(_app(_user(), session), STREAM.format(pid=uuid.uuid4()))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "live_unavailable"
    assert live.SLOTS.total() == 0, "no slot is held by a refused stream"


def _sentence(code: str) -> str:
    from app.services.activity.live_vocab import STREAM_ERROR_SENTENCES

    return STREAM_ERROR_SENTENCES[code]


async def _open(user, session=None, headers=None):
    return await activity_router.stream_activity(
        project_id=uuid.uuid4(),
        request=_request(headers or {"authorization": "Bearer x"}),
        db=session or _Session(),
        current_user=user,
        _=user,
    )


async def test_the_fourth_stream_of_one_user_is_429_and_a_finished_one_frees_its_slot(
    clock, redis
):
    user = _user()
    opened = [await _open(user) for _ in range(3)]
    with pytest.raises(Exception) as refused:
        await _open(user)
    assert refused.value.status_code == 429
    assert refused.value.detail == {"code": "too_many_streams", "message": _sentence("too_many_streams")}
    assert live.SLOTS.held(user.id) == 3
    # Another user still gets one: the cap is per user.
    await _open(_user())

    # One stream runs to its end (bye) -> its slot is released.
    chunks = [chunk async for chunk in opened[0].body_iterator]
    assert chunks[-1].startswith("event: bye")
    assert live.SLOTS.held(user.id) == 2
    await _open(user)
    assert live.SLOTS.held(user.id) == 3


async def test_the_process_cap_is_503_server_busy(redis, monkeypatch):
    monkeypatch.setattr(live.SLOTS, "per_process", 1)
    await _open(_user())
    with pytest.raises(Exception) as refused:
        await _open(_user())
    assert refused.value.status_code == 503
    assert refused.value.detail["code"] == "server_busy"


async def test_the_connection_is_released_before_the_response_and_headers_are_sse(clock, redis):
    session = _Session()
    response = await _open(_user(), session)
    assert session.closed == 1, "the pooled connection went back before the first byte"
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


async def test_the_route_is_matched_before_the_event_id_catch_all(redis, monkeypatch, clock):
    """``GET …/activity/stream`` is the stream, not a 422 from ``{event_id}``."""
    monkeypatch.setattr(activity_router, "_stream_lifetime", lambda request: (1, "max_age"))
    response = await _get(_app(_user(), _Session()), STREAM.format(pid=uuid.uuid4()))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("retry: 5000\n\n")
    assert "event: bye" in response.text


def test_the_lifetime_is_the_tokens_exp_when_sooner():
    from app.core.security import create_access_token

    soon = create_access_token(uuid.uuid4(), expires_delta=timedelta(minutes=5))
    later = create_access_token(uuid.uuid4(), expires_delta=timedelta(hours=2))
    seconds, reason = activity_router._stream_lifetime(_request({"authorization": f"Bearer {soon}"}))
    assert reason == "token_expired" and 290 <= seconds <= 300
    assert activity_router._stream_lifetime(_request({"authorization": f"Bearer {later}"})) == (
        900, "max_age",
    )


class _StreamRedis:
    """A Redis stream in memory: XRANGE (exclusive start, COUNT), XREVRANGE."""

    def __init__(self, ids: list[str], fail: bool = False) -> None:
        self.entries = [(i, {"payload": f'{{"id":"{i}"}}'}) for i in ids]
        self.fail = fail
        self.calls: list[tuple] = []

    def _range(self, start: str, count: int):
        if start == "-":
            return self.entries[:count]
        floor = live._id_key(start.lstrip("("))
        return [e for e in self.entries if live._id_key(e[0]) > floor][:count]

    async def xrevrange(self, key, maximum, minimum, count=None):
        self.calls.append(("xrevrange", key, maximum, minimum, count))
        if self.fail:
            raise ConnectionError("down")
        return list(reversed(self.entries))[:count]

    def pipeline(self, transaction=True):
        outer = self
        queued: list = []

        class _Pipe:
            def xrange(self, key, start, end, count=None):
                outer.calls.append(("xrange", key, start, end, count))
                queued.append((start, count))
                return self

            async def execute(self):
                if outer.fail:
                    raise ConnectionError("down")
                return [outer._range(start, count) for start, count in queued]

        return _Pipe()


async def test_stream_head_and_read_after_against_a_stream(monkeypatch):
    pid = uuid.uuid4()
    fake = _StreamRedis([f"100-{n}" for n in range(5)])
    monkeypatch.setattr(live, "get_redis", lambda: fake)

    assert await live.stream_head(pid) == "100-4"
    assert fake.calls[0] == ("xrevrange", f"testlookup:stream:activity:{pid}", "+", "-", 1)

    entries, gap = await live.read_after(pid, "100-1", limit=10)
    assert ([e[0] for e in entries], gap) == (["100-2", "100-3", "100-4"], False)
    assert ("xrange", f"testlookup:stream:activity:{pid}", "(100-1", "+", 11) in fake.calls

    entries, gap = await live.read_after(pid, "100-0", limit=2)
    assert gap is True and [e[0] for e in entries] == ["100-1", "100-2"], "more than limit behind"

    fake.entries = fake.entries[3:]  # 100-0 .. 100-2 trimmed away
    assert (await live.read_after(pid, "100-1"))[1] is True, "the client's id was trimmed"
    assert (await live.read_after(pid, "100-3"))[1] is False
    assert (await live.read_after(pid, "0-0"))[1] is False, "an empty stream's head is never trimmed"


async def test_an_empty_stream_head_is_0_0_and_redis_errors_are_live_unavailable(monkeypatch):
    pid = uuid.uuid4()
    monkeypatch.setattr(live, "get_redis", lambda: _StreamRedis([]))
    assert await live.stream_head(pid) == "0-0"
    monkeypatch.setattr(live, "get_redis", lambda: _StreamRedis([], fail=True))
    with pytest.raises(live.LiveUnavailable):
        await live.stream_head(pid)
    with pytest.raises(live.LiveUnavailable):
        await live.read_after(pid, "1-0")


def test_a_stale_slot_is_reclaimed_on_the_next_acquire():
    now = [1_000.0]
    slots = live.StreamSlots(per_user=1, per_process=10, clock=lambda: now[0])
    user_id = uuid.uuid4()
    assert slots.acquire(user_id) is None
    assert slots.acquire(user_id) == "too_many_streams"
    now[0] += live.MAX_STREAM_SECONDS + 61
    assert slots.acquire(user_id) is None, "a slot older than a stream's life is reclaimed"
