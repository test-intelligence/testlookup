"""E3 fix round C (m3, backend): ``/stream/active`` takes REPEATABLE ``suite_name``.

Before, ``suite_name`` was a scalar. A repeated key kept the LAST value, so the
Live page could not ask for several suites and filtered the page it got on the
client -- and the page is capped (``.limit(50)`` on each DB source), so a
matching session older than the newest 50 of the unfiltered set was never
shown. Now the server filters: OR within the suites, at most 50 distinct names
(C1 ``suite_cap`` / ``suite_name_length``, 422 with the analytics error body,
before any query), and ONE value is exactly the legacy statement. Authorisation
is unchanged (the project checks run before the service, as they did).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

P = "11111111-1111-4111-8111-111111111111"


class _Result:
    def scalars(self):
        return self

    def all(self):
        return []


class _Capture:
    def __init__(self):
        self.stmts: list = []

    async def execute(self, stmt, *a, **k):
        self.stmts.append(stmt)
        return _Result()


def _compiled(stmt) -> tuple[str, dict]:
    c = stmt.compile(dialect=postgresql.dialect())
    return str(c), {k: v for k, v in c.params.items() if not isinstance(v, datetime)}


async def _run(active=(), **kw) -> tuple[_Capture, object]:
    from app.services import stream_service

    db = _Capture()
    with patch(
        "app.streams.live_run_state.RedisLiveRunState.get_all_active",
        AsyncMock(return_value=list(active)),
    ):
        out = await stream_service.list_active_sessions(db, **kw)
    return db, out


def _legacy_statements(project_id, suite_name, days=7):
    """A FROZEN copy of the two DB statements ``list_active_sessions`` built
    before E3 (scalar ``suite_name``) -- the reference one suite must
    reproduce, text and parameters."""
    from app.models.postgres import LiveSession, Project, TestRun

    suite_key = (suite_name or "").strip().lower()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None
    stmt = (
        select(LiveSession)
        .where(
            LiveSession.status == "completed",
            LiveSession.project_id.in_(select(Project.id).where(Project.is_active.is_(True))),
        )
        .order_by(LiveSession.completed_at.desc())
        .limit(50)
    )
    if cutoff is not None:
        stmt = stmt.where(LiveSession.completed_at >= cutoff)
    if project_id:
        stmt = stmt.where(LiveSession.project_id == uuid.UUID(project_id))
    if suite_key:
        stmt = stmt.where(func.lower(func.trim(LiveSession.suite_name)) == suite_key)
    tr_stmt = (
        select(TestRun)
        .where(
            TestRun.trigger_source == "live_stream",
            TestRun.project_id.in_(select(Project.id).where(Project.is_active.is_(True))),
        )
        .order_by(TestRun.start_time.desc())
        .limit(50)
    )
    if cutoff is not None:
        tr_stmt = tr_stmt.where(TestRun.start_time >= cutoff)
    if project_id:
        tr_stmt = tr_stmt.where(TestRun.project_id == uuid.UUID(project_id))
    if suite_key:
        tr_stmt = tr_stmt.where(func.lower(func.trim(TestRun.primary_suite_name)) == suite_key)
    return stmt, tr_stmt


# ── the service ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("suite", ["payments", "  Payments ", ("Payments",), None, "   "])
@pytest.mark.parametrize("days", [7, 0])
async def test_one_suite_is_exactly_the_legacy_statements(suite, days):
    db, _ = await _run(project_id=P, suite_name=suite, days=days)
    legacy_name = suite[0] if isinstance(suite, tuple) else suite
    expected = _legacy_statements(P, legacy_name, days)
    got = db.stmts[1:3]  # [0] is the live-projects lookup
    assert [_compiled(s) for s in got] == [_compiled(s) for s in expected]


@pytest.mark.asyncio
async def test_several_suites_are_one_in_list_on_both_db_sources():
    db, _ = await _run(project_id=P, suite_name=("Payments", " cart ", "payments"))
    for stmt in db.stmts[1:3]:
        sql, params = _compiled(stmt)
        assert "lower(trim(" in sql and " IN (__[POSTCOMPILE_" in sql, sql
        in_values = [v for v in params.values() if isinstance(v, (list, tuple))]
        # Normalised and de-duplicated: payments / Payments / "payments" are one.
        assert sorted(in_values[-1]) == ["cart", "payments"], params


@pytest.mark.asyncio
async def test_several_suites_filter_the_redis_active_set():
    active = [
        {"project_id": P, "run_id": "r1", "suite_name": " Payments "},
        {"project_id": P, "run_id": "r2", "suite_name": "cart"},
        {"project_id": P, "run_id": "r3", "suite_name": "search"},
        {"project_id": P, "run_id": "r4", "suite_name": None},
    ]
    kept: list = []

    def _state(session):
        kept.append(session["run_id"])
        return SimpleNamespace(test_run_id=None)

    from app.services import stream_service

    class _Live(_Capture):
        async def execute(self, stmt, *a, **k):
            self.stmts.append(stmt)
            result = _Result()
            if len(self.stmts) == 1:  # the live-projects lookup
                result.all = lambda: [uuid.UUID(P)]  # type: ignore[method-assign]
            return result

    db = _Live()
    with patch(
        "app.streams.live_run_state.RedisLiveRunState.get_all_active", AsyncMock(return_value=active),
    ), patch.object(stream_service, "build_live_session_state", _state), \
         patch.object(stream_service, "ActiveSessionsResponse", lambda **kw: kw):
        await stream_service.list_active_sessions(db, project_id=P, suite_name=("payments", "CART"))
    assert kept == ["r1", "r2"]


# ── over HTTP ───────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """The real stream router on a bare app with the production exception
    handlers; the user and the database overridden."""
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.testclient import TestClient
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.core import analytics_errors as errors
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import stream

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, errors.http_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_exception_handler)
    app.add_exception_handler(errors.AnalyticsQueryError, errors.analytics_query_error_handler)
    app.include_router(stream.router)

    db = AsyncMock()

    async def _db():
        yield db

    user = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", is_active=True, api_key_project_id=None)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    yield TestClient(app, raise_server_exceptions=False), db


def _service():
    from app.models.schemas import ActiveSessionsResponse

    return AsyncMock(return_value=ActiveSessionsResponse(sessions=[], count=0))


@pytest.mark.parametrize("accessible", [None, {uuid.UUID(P)}])
@pytest.mark.parametrize(
    "params, expected",
    [
        ([("suite_name", "Payments")], "Payments"),
        ([("suite_name", "Payments"), ("suite_name", "cart")], ("Payments", "cart")),
        ([("suite_name", "a"), ("suite_name", "a")], "a"),
        ([], None),
    ],
)
def test_every_suite_reaches_the_service(client, params, expected, accessible):
    """Repeated keys arrive as a list (a scalar kept only the last); one value
    reaches the service as the same string it always did."""
    http, _db = client
    svc = _service()
    with patch("app.routers.stream.get_accessible_project_ids", AsyncMock(return_value=accessible)), \
         patch("app.routers.stream.stream_service.list_active_sessions", svc):
        resp = http.get("/api/v1/stream/active", params=[("project_id", P), *params])
    assert resp.status_code == 200, resp.text
    assert svc.await_args.kwargs["suite_name"] == expected


@pytest.mark.parametrize(
    "params, code",
    [
        ([("suite_name", f"s{i}") for i in range(51)], "suite_cap"),
        ([("suite_name", "a"), ("suite_name", "x" * 501)], "suite_name_length"),
        ([("suite_name", "")], "suite_name_length"),
    ],
)
def test_c1_violations_are_422_with_the_contract_body_before_any_query(client, params, code):
    http, db = client
    svc = _service()
    access = AsyncMock(return_value=None)
    with patch("app.routers.stream.get_accessible_project_ids", access), \
         patch("app.routers.stream.stream_service.list_active_sessions", svc):
        resp = http.get("/api/v1/stream/active", params=params)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == code and body["param"] == "suite_name", body
    svc.assert_not_awaited()
    access.assert_not_awaited()
    db.execute.assert_not_awaited()


def test_the_cap_admits_fifty(client):
    http, _db = client
    svc = _service()
    with patch("app.routers.stream.get_accessible_project_ids", AsyncMock(return_value=None)), \
         patch("app.routers.stream.stream_service.list_active_sessions", svc):
        resp = http.get("/api/v1/stream/active", params=[("suite_name", f"s{i}") for i in range(50)])
    assert resp.status_code == 200, resp.text
    assert len(svc.await_args.kwargs["suite_name"]) == 50


def test_authorisation_is_unchanged(client):
    """A non-member naming a project is still the 403, suites or not."""
    http, _db = client
    svc = _service()
    with patch("app.routers.stream.get_accessible_project_ids", AsyncMock(return_value=set())), \
         patch("app.routers.stream.stream_service.list_active_sessions", svc):
        resp = http.get("/api/v1/stream/active",
                        params=[("project_id", P), ("suite_name", "a"), ("suite_name", "b")])
    assert resp.status_code == 403
    svc.assert_not_awaited()
