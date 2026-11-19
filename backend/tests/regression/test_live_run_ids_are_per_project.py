"""A live run id belongs to the project whose key sent it, not to the deployment.

The rule: on ``POST /ws/events/{run_id}`` with a project-bound API key, the
run-to-project binding in Redis is scoped to that project. Two projects may use
the same ``run_id`` ("build-42") at the same time; neither is refused, neither
can reserve ids to block the other, and neither can learn from the answer
whether the other is using an id.

What stays as it was:

* the legacy shared-secret path names no project server-side, so its binding
  stays deployment-wide and a second project naming the same id still gets 409;
* a binding written in the old, deployment-wide shape (a run that started
  before the upgrade, or a legacy-path run) is still honoured when a
  project-key event arrives for an id that project has not started itself:
  another project's event is refused with 403;
* an event is only ever admitted under the key's own project.

These drive the real handler and the real ``live_event_authz`` against an
in-memory Redis; only the transport after authorisation is stubbed.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.routers.live import ingest_live_event
from app.services import live_event_authz as authz

pytestmark = pytest.mark.regression

PROJECT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PROJECT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
RUN = "build-42"
FAKE_SHARED_SECRET = "fake-shared-secret-for-tests"


class _Redis:
    """SET NX / GET with redis-py's return values."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def get(self, key):
        return self.store.get(key)


@pytest.fixture
def wired(monkeypatch):
    redis = _Redis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)

    admitted: list[tuple[str, str, str]] = []  # (project, run_id, event type)

    async def _admit(_db, *, project_id, api_key_name, run_id, event, api_key=None):
        from app.services.ws_event_ingest import WsIngestOutcome

        admitted.append((str(project_id), run_id, event.get("type")))
        return WsIngestOutcome("session-1")

    monkeypatch.setattr("app.services.ws_event_ingest.ingest_one", _admit)

    legacy_published: list[tuple[str, dict]] = []

    async def _publish(run_id, event):
        legacy_published.append((run_id, event))
        return "1-0"

    monkeypatch.setattr("app.streams.producer.publish_live_event", _publish)

    class _Coll:
        async def insert_one(self, _doc):
            return None

    monkeypatch.setattr(
        "app.db.mongo.get_mongo_db", lambda: {"live_execution_events": _Coll()}
    )

    async def _noop(*_args, **_kwargs):
        return None

    for target in (
        "app.streams.live_run_state.RedisLiveRunState.start",
        "app.streams.live_run_state.RedisLiveRunState.record_test_event",
        "app.services.ingestion_backpressure.enforce_redis_memory_backpressure",
        "app.services.ingestion_rate_limit.enforce_live_event_rate_limit",
        "app.services.live_event_authz.remember_streaming_project",
    ):
        monkeypatch.setattr(target, _noop)
    monkeypatch.setattr(
        "app.services.live_event_authz.cached_streaming_project",
        AsyncMock(return_value=None),
    )

    # Each fake key is bound to one project, as a real project key is.
    keys = {"fake-key-a": PROJECT_A, "fake-key-b": PROJECT_B}

    async def _key_context(*, db, x_api_key):
        return SimpleNamespace(project_id=keys[x_api_key], api_key_name="ci")

    monkeypatch.setattr("app.routers.live.get_streaming_api_key_context", _key_context)
    monkeypatch.setattr(settings, "LIVE_EVENTS_REQUIRE_PROJECT_KEY", False)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", FAKE_SHARED_SECRET)
    return SimpleNamespace(redis=redis, admitted=admitted, legacy_published=legacy_published)


async def _send(event_type="test_result", *, key=None, secret=None, project=None, run_id=RUN):
    event = {"type": event_type, "test_name": "t", "status": "PASSED", "build_number": "1"}
    if project:
        event["project_id"] = project
    return await ingest_live_event(
        run_id=run_id,
        event=event,
        x_api_key=key,
        x_webhook_secret=secret,
        db=SimpleNamespace(),
    )


# ── project keys: one namespace per project ──────────────────────────────────


@pytest.mark.asyncio
async def test_two_projects_can_start_a_run_with_the_same_id(wired):
    first = await _send("run_start", key="fake-key-a")
    second = await _send("run_start", key="fake-key-b")

    assert first["accepted"] is True
    assert second["accepted"] is True, "project B was refused a run id project A is using"
    assert wired.admitted == [
        (PROJECT_A, RUN, "run_start"),
        (PROJECT_B, RUN, "run_start"),
    ]
    # Each run is bound to its own project, under its own key.
    assert wired.redis.store[authz.run_project_key(RUN, PROJECT_A)] == PROJECT_A
    assert wired.redis.store[authz.run_project_key(RUN, PROJECT_B)] == PROJECT_B
    assert await authz.resolve_run_project(RUN, project_id=PROJECT_A) == PROJECT_A
    assert await authz.resolve_run_project(RUN, project_id=PROJECT_B) == PROJECT_B


@pytest.mark.asyncio
async def test_a_project_key_writes_no_deployment_wide_binding(wired):
    """Nothing a project key does may reserve the id for everyone."""
    await _send("run_start", key="fake-key-a")

    assert authz.run_project_key(RUN) not in wired.redis.store


@pytest.mark.asyncio
async def test_the_same_project_may_start_and_continue_its_run(wired):
    await _send("run_start", key="fake-key-a")
    await _send("run_start", key="fake-key-a")  # a retried run_start
    result = await _send("test_result", key="fake-key-a")

    assert result["accepted"] is True
    assert [entry[0] for entry in wired.admitted] == [PROJECT_A] * 3


@pytest.mark.asyncio
async def test_an_event_is_admitted_only_under_the_keys_own_project(wired):
    """Project B sending a result for an id project A started.

    It no longer learns that A uses the id (the old 403 was that oracle). The
    event goes to B's own run of that name and never reaches A's.
    """
    await _send("run_start", key="fake-key-a")

    await _send("test_result", key="fake-key-b")

    assert wired.admitted == [
        (PROJECT_A, RUN, "run_start"),
        (PROJECT_B, RUN, "test_result"),
    ]


# ── the legacy shared-secret path stays deployment-wide ──────────────────────


@pytest.mark.asyncio
async def test_the_shared_secret_path_still_refuses_a_second_project(wired):
    await _send("run_start", secret=FAKE_SHARED_SECRET, project=PROJECT_A)

    with pytest.raises(HTTPException) as refused:
        await _send("run_start", secret=FAKE_SHARED_SECRET, project=PROJECT_B)

    assert refused.value.status_code == 409
    assert "already in use" in refused.value.detail
    assert wired.redis.store[authz.run_project_key(RUN)] == PROJECT_A
    assert len(wired.legacy_published) == 1


@pytest.mark.asyncio
async def test_a_shared_secret_run_cannot_block_a_project_keys_run_start(wired):
    await _send("run_start", secret=FAKE_SHARED_SECRET, project=PROJECT_A)

    result = await _send("run_start", key="fake-key-b")

    assert result["accepted"] is True


# ── bindings written in the old shape are still read ─────────────────────────


@pytest.mark.asyncio
async def test_an_old_shape_binding_still_refuses_another_projects_event(wired):
    """A run that started before the upgrade is bound deployment-wide to A."""
    wired.redis.store[authz.run_project_key(RUN)] = PROJECT_A

    with pytest.raises(HTTPException) as refused:
        await _send("test_result", key="fake-key-b")

    assert refused.value.status_code == 403
    assert wired.admitted == []


@pytest.mark.asyncio
async def test_an_old_shape_binding_lets_its_own_project_continue(wired):
    wired.redis.store[authz.run_project_key(RUN)] = PROJECT_A

    result = await _send("test_result", key="fake-key-a")

    assert result["accepted"] is True
    assert wired.admitted == [(PROJECT_A, RUN, "test_result")]


@pytest.mark.asyncio
async def test_a_projects_own_binding_wins_over_an_old_shape_one(wired):
    """Once B has started the id itself, an old binding for A is not consulted."""
    wired.redis.store[authz.run_project_key(RUN)] = PROJECT_A
    await _send("run_start", key="fake-key-b")

    result = await _send("test_result", key="fake-key-b")

    assert result["accepted"] is True


# ── the key shape ────────────────────────────────────────────────────────────


def test_a_crafted_run_id_cannot_alias_a_project_scoped_key():
    """``run_id`` is caller-chosen and may contain colons. A deployment-wide key
    built from one must never equal another project's scoped key."""
    scoped = authz.run_project_key(RUN, PROJECT_A)
    for crafted in (f"{PROJECT_A}:{RUN}", f"p:{PROJECT_A}:{RUN}", scoped, scoped.split(":", 3)[-1]):
        assert authz.run_project_key(crafted) != scoped


def test_scoped_keys_differ_per_project_and_from_the_deployment_wide_key():
    keys = {
        authz.run_project_key(RUN),
        authz.run_project_key(RUN, PROJECT_A),
        authz.run_project_key(RUN, PROJECT_B),
    }
    assert len(keys) == 3
