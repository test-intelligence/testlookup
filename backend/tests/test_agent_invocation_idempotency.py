"""E1.3: Idempotency-Key on POST /api/v1/agents/{agent_id}/invoke.

* same key and same request -> the invocation it created, with 200, even if it
  has since failed. Nothing new is created or dispatched;
* same key and a different request -> 422;
* same key while the first request is still being handled -> 409 + Retry-After;
* a key never resolves across users or projects;
* Redis unavailable -> no lock, and a racing duplicate loses at the unique
  index and is answered with the invocation that won;
* a request that fails before committing frees its key.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.exc import IntegrityError

from app.services import invocation_idempotency as idem

# -- fingerprint and lock key ----------------------------------------------------------


def test_the_fingerprint_ignores_key_order_and_covers_project_and_input():
    project = str(uuid.uuid4())
    body = {"project_id": project, "input": {"agent_id": "agent.summary.v1", "payload": {"test_run_id": "r"}}}
    reordered = {"input": {"payload": {"test_run_id": "r"}, "agent_id": "agent.summary.v1"}, "project_id": project}
    assert idem.request_fingerprint("agent.summary.v1", body) == idem.request_fingerprint("agent.summary.v1", reordered)
    assert idem.request_fingerprint("agent.summary.v1", body) != idem.request_fingerprint(
        "agent.summary.v1", {**body, "project_id": str(uuid.uuid4())},
    )
    assert idem.request_fingerprint("agent.summary.v1", body) != idem.request_fingerprint("agent.triage.v1", body)


def test_the_lock_key_is_scoped_by_user_and_project_and_hides_the_client_key():
    key = "018f3c2e-7b1a-7c1d-9e4f-2a6b8c0d1e2f"
    a = idem.lock_key("user-a", "project-1", "agent.summary.v1", key)
    assert key not in a
    assert a != idem.lock_key("user-b", "project-1", "agent.summary.v1", key)
    assert a != idem.lock_key("user-a", "project-2", "agent.summary.v1", key)


# -- the Redis lock -----------------------------------------------------------------------


class _Redis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False, xx=False, keepttl=False):
        if nx and key in self.store:
            return None
        if xx and key not in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr(idem, "get_redis", lambda: fake)
    return fake


ARGS = ("user-1", "project-1", "agent.summary.v1", "key-12345678")


@pytest.mark.asyncio
async def test_the_lock_lifecycle(redis):
    assert await idem.claim(*ARGS, "fp-1") == "claimed"
    assert await idem.claim(*ARGS, "fp-1") == "in_flight"
    assert await idem.claim(*ARGS, "fp-2") == "conflict"
    await idem.complete(*ARGS, "fp-1", "inv-1")
    assert await idem.claim(*ARGS, "fp-1") == "done"
    assert await idem.claim(*ARGS, "fp-2") == "conflict"


@pytest.mark.asyncio
async def test_release_frees_only_a_pending_lock_of_the_same_request(redis):
    assert await idem.claim(*ARGS, "fp-1") == "claimed"
    await idem.release(*ARGS, "fp-other")
    assert await idem.claim(*ARGS, "fp-1") == "in_flight", "another request's release must not free it"
    await idem.release(*ARGS, "fp-1")
    assert await idem.claim(*ARGS, "fp-1") == "claimed"

    await idem.complete(*ARGS, "fp-1", "inv-1")
    await idem.release(*ARGS, "fp-1")
    assert await idem.claim(*ARGS, "fp-1") == "done", "a completed key is never freed"


@pytest.mark.asyncio
async def test_the_lock_degrades_to_unavailable_when_redis_is_down(monkeypatch):
    broken = SimpleNamespace(set=AsyncMock(side_effect=ConnectionError()), get=AsyncMock(), delete=AsyncMock())
    monkeypatch.setattr(idem, "get_redis", lambda: broken)
    assert await idem.claim(*ARGS, "fp-1") == "unavailable"
    await idem.complete(*ARGS, "fp-1", "inv-1")
    await idem.release(*ARGS, "fp-1")


# -- the route -------------------------------------------------------------------------------


KEY = "018f3c2e-7b1a-7c1d-9e4f-2a6b8c0d1e2f"


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value, all=lambda: [] if self._value is None else [self._value])


@pytest.fixture
def route(monkeypatch):
    pytest.importorskip("celery")
    from app.routers import agent_invoke as router
    from app.worker import tasks

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="42")
    user = SimpleNamespace(id=uuid.uuid4())
    db = MagicMock()
    db.execute = AsyncMock(return_value=_Result(run))
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    dispatch = MagicMock()
    monkeypatch.setattr(router, "resolve_project_scope", AsyncMock())
    from app.services import agent_config_resolver
    monkeypatch.setattr(router, "resolve_for_project", AsyncMock(
        side_effect=lambda _db, _project, agent, **_kw: agent_config_resolver.resolve(agent, global_ai_config={})
    ))
    monkeypatch.setattr(router, "record_activity", AsyncMock())
    monkeypatch.setattr(router, "ActorRef", SimpleNamespace(from_user=lambda _u: "actor"))
    monkeypatch.setattr(router, "_in_progress_invocation", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "_idempotent_replay", AsyncMock(return_value=None))
    monkeypatch.setattr(router.invocation_idempotency, "claim", AsyncMock(return_value="claimed"))
    monkeypatch.setattr(router.invocation_idempotency, "complete", AsyncMock())
    monkeypatch.setattr(router.invocation_idempotency, "release", AsyncMock())
    monkeypatch.setattr(tasks.run_agent_invocation, "apply_async", dispatch)

    async def call(key=KEY):
        body = router.AgentInvokeRequest(
            project_id=run.project_id,
            input={"agent_id": "agent.summary.v1", "payload": {"test_run_id": str(run.id)}},
        )
        response = Response()
        response.status_code = 202
        out = await router.invoke_agent(
            agent_id="agent.summary.v1", body=body, response=response, db=db,
            current_user=user, idempotency_key=key,
        )
        return out, response

    return SimpleNamespace(router=router, db=db, dispatch=dispatch, call=call, run=run, user=user)


def _view(**overrides):
    base = {"id": uuid.uuid4(), "status": "failed", "links": {"self": "x"}}
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_a_replayed_request_returns_the_invocation_it_created_even_if_it_failed(route):
    stored = _view(status="failed")
    route.router._idempotent_replay.return_value = stored

    out, response = await route.call()

    assert out is stored and response.status_code == 200
    route.db.add.assert_not_called()
    route.dispatch.assert_not_called()
    route.router.invocation_idempotency.claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_key_reused_for_a_different_request_is_422(route):
    route.router.invocation_idempotency.claim.return_value = "conflict"

    with pytest.raises(HTTPException) as exc:
        await route.call()
    assert exc.value.status_code == 422
    route.db.add.assert_not_called()


@pytest.mark.asyncio
async def test_a_key_still_being_handled_is_409_with_retry_after(route):
    route.router.invocation_idempotency.claim.return_value = "in_flight"

    with pytest.raises(HTTPException) as exc:
        await route.call()
    assert exc.value.status_code == 409 and exc.value.headers["Retry-After"]
    route.db.add.assert_not_called()
    route.router.invocation_idempotency.release.assert_not_awaited(), "the lock belongs to the first request"


@pytest.mark.asyncio
async def test_a_done_lock_reads_the_committed_invocation_back(route):
    stored = _view(status="in_progress")
    route.router.invocation_idempotency.claim.return_value = "done"
    route.router._idempotent_replay.side_effect = [None, stored]

    out, response = await route.call()
    assert out is stored and response.status_code == 200
    route.db.add.assert_not_called()


@pytest.mark.asyncio
async def test_a_first_request_stores_its_key_commits_marks_the_key_done_then_dispatches(route):
    order: list[str] = []
    route.db.commit.side_effect = lambda: order.append("commit")
    route.router.invocation_idempotency.complete.side_effect = lambda *a, **k: order.append("complete")
    route.dispatch.side_effect = lambda **_k: order.append("dispatch")

    out, response = await route.call()

    added = route.db.add.call_args.args[0]
    assert added.idempotency_key == KEY and added.request_sha256 and added.requested_by == route.user.id
    assert order == ["commit", "complete", "dispatch"]
    assert route.router.invocation_idempotency.complete.await_args.args[-1] == added.id
    route.router.invocation_idempotency.release.assert_not_awaited()
    assert response.status_code == 202 and out["id"] == added.id


@pytest.mark.asyncio
async def test_without_redis_a_racing_duplicate_loses_at_the_index_and_gets_the_winner(route):
    winner = _view(status="in_progress")
    route.router.invocation_idempotency.claim.return_value = "unavailable"
    route.db.commit.side_effect = IntegrityError("INSERT", {}, Exception("duplicate key"))
    route.router._idempotent_replay.side_effect = [None, winner]

    out, response = await route.call()

    route.db.rollback.assert_awaited_once()
    assert out is winner and response.status_code == 200
    route.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_an_integrity_error_that_is_not_the_key_still_raises(route):
    route.db.commit.side_effect = IntegrityError("INSERT", {}, Exception("other constraint"))
    with pytest.raises(IntegrityError):
        await route.call(key=None)


@pytest.mark.asyncio
async def test_a_request_that_fails_before_committing_frees_its_key(route):
    route.router.record_activity.side_effect = RuntimeError("ledger down")
    with pytest.raises(RuntimeError):
        await route.call()
    route.router.invocation_idempotency.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_key_means_no_lookup_and_no_lock(route):
    await route.call(key=None)
    route.router._idempotent_replay.assert_not_awaited()
    route.router.invocation_idempotency.claim.assert_not_awaited()
    assert route.db.add.call_args.args[0].idempotency_key is None


@pytest.mark.asyncio
async def test_the_replay_lookup_is_scoped_to_the_user_and_refuses_a_different_request(monkeypatch):
    from app.routers import agent_invoke as router

    stored = SimpleNamespace(request_sha256="fp-original")
    db = MagicMock()
    db.execute = AsyncMock(return_value=_Result(stored))
    monkeypatch.setattr(router, "_invocation_view", AsyncMock(return_value={"id": "x"}))

    assert await router._idempotent_replay(db, uuid.uuid4(), KEY, "fp-original") == {"id": "x"}
    # The WHERE clause, not the whole statement: every column name appears in the SELECT list.
    where = str(db.execute.await_args.args[0].whereclause)
    assert "agent_invocations.requested_by" in where and "agent_invocations.idempotency_key" in where
    with pytest.raises(HTTPException) as exc:
        await router._idempotent_replay(db, uuid.uuid4(), KEY, "fp-different")
    assert exc.value.status_code == 422
    assert await router._idempotent_replay(db, None, KEY, "fp-original") is None


def test_the_header_is_optional_for_direct_callers_and_validated_for_http():
    import typing

    from app.routers.agent_invoke import invoke_agent

    assert inspect.signature(invoke_agent).parameters["idempotency_key"].default is None
    # The router uses postponed annotations, so resolve them to reach the Header.
    header = typing.get_type_hints(invoke_agent, include_extras=True)["idempotency_key"].__metadata__[0]
    assert header.alias == "Idempotency-Key"


def test_the_migration_builds_the_unique_index_concurrently():
    source = (
        Path(__file__).resolve().parents[1] / "migrations/versions/0178_agent_invocation_idempotency.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision = "0177"' in source
    assert "autocommit_block" in source and "postgresql_concurrently=True" in source
    assert '["requested_by", "idempotency_key"]' in source and "unique=True" in source


def test_the_model_declares_the_same_partial_unique_index():
    from app.models.postgres import AgentInvocation

    index = next(i for i in AgentInvocation.__table__.indexes if i.name == "ux_agent_invocations_user_idempotency_key")
    assert index.unique and [c.name for c in index.columns] == ["requested_by", "idempotency_key"]
    assert "idempotency_key IS NOT NULL" in str(index.dialect_options["postgresql"]["where"])
    assert isinstance(datetime.now(timezone.utc), datetime)
