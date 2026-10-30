"""W23d AGT-14: the two HTTP-exchange routes, at unit level (fake session, fake Mongo).

* ``GET /api/v1/runs/{run_id}/tests/{test_id}/http-exchanges`` — ``require_run_access``
  on the run, the case must belong to THAT run (the select binds both ids; another
  run's case is the same 404 as an unknown id), a Mongo outage is a 503 with its
  sentence, and the GET writes nothing.
* ``POST /api/v1/stream/http-exchanges`` — the session id comes from the BODY and is
  loaded (404), a key bound to another project is refused (403), the caller must be a
  member of the SESSION's project (the real ``resolve_project_scope``), the body
  ``run_id`` must be the session's (409), and the stored ``test_case_id`` is the id
  ``_event_to_row`` gives the same ``test_result`` event.

The same routes over real Postgres + Mongo are
``tests/integration/test_http_exchanges_postgres_mongo.py``.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_api_key_context, get_current_active_user
from app.db.postgres import get_db
from app.services import http_exchange_store as store
from app.services.live_session_drainer import _event_to_row, live_test_case_id
from tests.test_http_exchange_store import BEARER, FakeCollection, _exchange

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_PROJECT = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
OTHER_RUN = uuid.UUID("66666666-6666-4666-8666-666666666666")
CASE = uuid.UUID("44444444-4444-4444-8444-444444444444")
SESSION = uuid.UUID("55555555-5555-4555-8555-555555555555")
CASES_BY_RUN = {(CASE, RUN)}


def _user(role: str = "QA_ENGINEER") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, username="member")


def _scalar(value) -> MagicMock:
    return MagicMock(scalar_one_or_none=MagicMock(return_value=value))


@pytest.fixture
def mongo(monkeypatch) -> FakeCollection:
    fake = FakeCollection()
    monkeypatch.setattr(store, "get_mongo_db", lambda: {"rest_api_payloads": fake})
    return fake


@pytest.fixture
def world(monkeypatch, mongo):
    from app.routers import runs, stream

    state = {
        "user": _user(), "bound": None, "member": True, "run_project": PROJECT,
        "session": SimpleNamespace(
            id=SESSION, project_id=PROJECT, run_id="local-w23dsample", build_number="41",
            started_at=datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc),
        ),
        "selects": [],
    }
    db = AsyncMock()

    async def execute(stmt, *a, **k):
        sql = str(stmt)
        if "project_members" in sql:
            return _scalar(uuid.uuid4() if state["member"] else None)
        if "test_cases" in sql:
            params = stmt.compile().params
            state["selects"].append(params)
            test_id = next(v for k_, v in params.items() if k_.startswith("id_"))
            run_id = next(v for k_, v in params.items() if k_.startswith("test_run_id_"))
            row = SimpleNamespace(id=test_id, project_id=state["run_project"]) if (test_id, run_id) in CASES_BY_RUN else None
            return MagicMock(first=MagicMock(return_value=row))
        if "test_runs" in sql:
            return _scalar(state["run_project"])
        raise AssertionError(sql)

    async def get(_model, key):
        session = state["session"]
        return session if session is not None and key == session.id else None

    db.execute = AsyncMock(side_effect=execute)
    db.get = AsyncMock(side_effect=get)

    async def _db():
        yield db

    async def _accessible(_db, user):
        return {PROJECT} if state["member"] else set()

    app = FastAPI()
    app.include_router(runs.router)
    app.include_router(stream.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    app.dependency_overrides[get_api_key_context] = lambda: (state["user"], state["bound"])
    monkeypatch.setattr("app.core.deps.get_accessible_project_ids", _accessible)
    monkeypatch.setattr(stream, "enforce_redis_memory_backpressure", AsyncMock())
    monkeypatch.setattr(stream, "enforce_ingest_rate_limit", AsyncMock())
    state["app"] = app
    return state


async def _call(world, method: str, path: str, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=world["app"]), base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _get_path(run=RUN, case=CASE) -> str:
    return f"/api/v1/runs/{run}/tests/{case}/http-exchanges"


async def _seed():
    await store.write_exchanges(
        project_id=str(PROJECT), test_run_id=str(RUN), build_number="41",
        run_created_at=datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc),
        items=[(str(CASE), [_exchange(1), _exchange(2)], 1)], source="json_batch",
    )


# ── GET …/http-exchanges ─────────────────────────────────────────────────────


async def test_a_viewer_member_reads_the_redacted_list_and_the_get_writes_nothing(world, mongo):
    world["user"] = _user("VIEWER")
    await _seed()
    mongo.calls.clear()
    resp = await _call(world, "GET", _get_path())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["total"], body["dropped"], body["run_cap_reached"]) == (2, 1, False)
    first = body["items"][0]
    assert first["seq"] == 0 and first["method"] == "GET" and first["endpoint"] == "/api/products/{id}"
    assert first["request"]["headers"]["Authorization"] == "[REDACTED]" and first["redacted"] is True
    assert first["source"] == "json_batch" and first["status"] == 200
    assert BEARER.split()[1] not in resp.text
    assert not {"insert_many", "delete_many"} & set(mongo.calls)
    assert world["selects"] == [{"id_1": CASE, "test_run_id_1": RUN}]


async def test_another_runs_case_is_the_same_404_as_an_unknown_id(world, mongo):
    await _seed()
    mongo.calls.clear()
    for path in (_get_path(run=RUN, case=uuid.uuid4()), _get_path(run=OTHER_RUN, case=CASE)):
        resp = await _call(world, "GET", path)
        assert resp.status_code == 404 and resp.json()["detail"] == "Test case not found"
    assert mongo.calls == []


async def test_a_non_member_is_refused_by_the_run_guard_before_any_read(world, mongo):
    world["user"] = _user("VIEWER")
    world["member"] = False
    resp = await _call(world, "GET", _get_path())
    assert resp.status_code == 404 and resp.json()["detail"] == "Test run not found"
    assert world["selects"] == [] and mongo.calls == []


async def test_nothing_captured_is_an_empty_list_with_zero_total(world, mongo):
    resp = await _call(world, "GET", _get_path())
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "dropped": 0, "run_cap_reached": False}


async def test_a_mongo_outage_is_a_503_with_its_sentence_never_an_empty_list(world, mongo):
    mongo.fail = {"*"}
    resp = await _call(world, "GET", _get_path())
    assert resp.status_code == 503
    assert resp.json()["detail"] == {
        "code": "exchanges_unavailable", "message": "HTTP exchanges could not be read right now.",
    }


# ── POST /api/v1/stream/http-exchanges ───────────────────────────────────────


def _live_body(**overrides) -> dict:
    body = {
        "session_id": str(SESSION), "run_id": "local-w23dsample",
        "test_name": "test_product_price", "class_name": "tests.test_products",
        "exchanges": [_exchange(1), _exchange(2)],
    }
    body.update(overrides)
    return body


async def test_the_live_route_stores_under_the_drainers_own_test_case_id(world, mongo):
    resp = await _call(world, "POST", "/api/v1/stream/http-exchanges", json=_live_body())
    assert resp.status_code == 202, resp.text
    event = {"event_type": "test_result", "test_name": "test_product_price",
             "class_name": "tests.test_products", "status": "FAILED"}
    expected = str(_event_to_row(event, run_uuid=SESSION, default_suite=None)["id"])
    assert resp.json() == {"stored": 2, "truncated": 0, "dropped": 0, "test_case_id": expected}
    assert {d["test_case_id"] for d in mongo.docs} == {expected}
    doc = mongo.docs[0]
    assert (doc["project_id"], doc["test_run_id"], doc["source"]) == (str(PROJECT), str(SESSION), "live")
    assert doc["build_number"] == "41" and doc["request"]["headers"]["Authorization"] == "[REDACTED]"
    assert BEARER.split()[1] not in json.dumps(mongo.docs, default=str)


async def test_a_malformed_live_exchange_is_dropped_never_a_422(world, mongo):
    body = _live_body(exchanges=[_exchange(1), {"method": "get me", "url": "https://api.example.test/x"}])
    resp = await _call(world, "POST", "/api/v1/stream/http-exchanges", json=body)
    assert resp.status_code == 202, resp.text
    assert (resp.json()["stored"], resp.json()["dropped"]) == (1, 1)


@pytest.mark.parametrize("session_id", ["not-a-uuid", "77777777-7777-4777-8777-777777777777"])
async def test_an_unknown_session_is_a_404_and_writes_nothing(world, mongo, session_id):
    resp = await _call(world, "POST", "/api/v1/stream/http-exchanges", json=_live_body(session_id=session_id))
    assert resp.status_code == 404 and resp.json()["detail"] == "Session not found"
    assert mongo.calls == []


async def test_a_key_bound_to_another_project_is_refused(world, mongo):
    world["bound"] = OTHER_PROJECT
    resp = await _call(world, "POST", "/api/v1/stream/http-exchanges", json=_live_body())
    assert resp.status_code == 403 and resp.json()["detail"] == "This API key is restricted to a different project"
    assert mongo.calls == []


async def test_a_non_member_of_the_sessions_project_is_refused(world, mongo):
    world["member"] = False
    resp = await _call(world, "POST", "/api/v1/stream/http-exchanges", json=_live_body())
    assert resp.status_code == 403 and resp.json()["detail"] == "You do not have access to this project"
    assert mongo.calls == []


async def test_another_sessions_run_id_is_a_409(world, mongo):
    resp = await _call(world, "POST", "/api/v1/stream/http-exchanges", json=_live_body(run_id="local-other"))
    assert resp.status_code == 409 and resp.json()["detail"] == "run_id does not belong to this live session"
    assert mongo.calls == []


async def test_a_viewer_cannot_post_exchanges(world, mongo):
    world["user"] = _user("VIEWER")
    resp = await _call(world, "POST", "/api/v1/stream/http-exchanges", json=_live_body())
    assert resp.status_code == 403
    assert mongo.calls == []


@pytest.mark.parametrize(
    "test_name, class_name",
    [("test_checkout", None), ("test with\x00nul", "pkg.Cls"), ("t" * 1500, "c" * 800), ("ghp_" + "a1B2" * 9, None)],
    ids=["no-class", "nul-byte", "over-long", "token-shaped-name"],
)
def test_live_test_case_id_equals_the_drainers_id(test_name, class_name):
    event = {"event_type": "test_result", "test_name": test_name, "class_name": class_name, "status": "PASSED"}
    run_uuid = uuid.uuid4()
    assert live_test_case_id(run_uuid, test_name, class_name) == _event_to_row(
        event, run_uuid=run_uuid, default_suite=None,
    )["id"]
