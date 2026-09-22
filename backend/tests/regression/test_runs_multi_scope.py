"""E3 (VIZ-303 follow-up): ``/runs`` and ``/me/assigned-failures`` take
REPEATABLE ``release_id`` / ``suite_name``.

Before, both declared the two as scalars. FastAPI binds a repeated key to a
scalar by keeping the LAST value, so the frontend had to drop a multi-selection
on these routes -- and the run list and the inbox showed every release while
the header named several. Now: OR within a dimension, AND across, C1 caps
(20 releases / 50 suites, 422 with the analytics error body), every release id
authorised by the shared resolver, and ONE value is exactly the legacy call.

The real-database proof (OR/AND on rows, the forbidden id, one-value results)
is ``tests/integration/test_runs_inbox_multi_scope_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.dialects import postgresql

from app.core.release_filter import UNATTRIBUTED
from app.services import analytics_scope as scope_mod

pytestmark = pytest.mark.regression

P = "11111111-1111-4111-8111-111111111111"
R1 = "22222222-2222-4222-8222-222222222221"
R2 = "22222222-2222-4222-8222-222222222222"


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


# ── the suite rule moved, one value compiles to the same text ─────────────────


def _legacy_run_suite_filter(suite_name):
    """A FROZEN copy of ``runs_service._run_suite_filter`` before E3 -- the
    reference the moved clause must reproduce for one suite, text for text."""
    from app.models.postgres import TestCase, TestRun

    suite_key = (suite_name or "").strip().lower()
    if not suite_key:
        return None
    case_exists = (
        select(TestCase.id)
        .where(
            TestCase.test_run_id == TestRun.id,
            func.lower(func.trim(func.coalesce(TestCase.suite_name, "Unknown Suite"))) == suite_key,
        )
        .exists()
    )
    return or_(
        func.lower(func.trim(func.coalesce(TestRun.primary_suite_name, "Unknown Suite"))) == suite_key,
        case_exists,
    )


def _where(clause) -> str:
    from app.models.postgres import TestRun

    return _sql(select(TestRun.id).where(clause))


@pytest.mark.parametrize("name", ["Payments", "  payments ", "Unknown Suite"])
def test_one_suite_compiles_exactly_as_the_legacy_filter(name):
    assert _where(scope_mod.run_list_suite_clause(name)) == _where(_legacy_run_suite_filter(name))
    # A one-element tuple is the same request.
    assert _where(scope_mod.run_list_suite_clause((name,))) == _where(_legacy_run_suite_filter(name))


def test_several_suites_are_one_in_list_on_both_arms():
    sql = _where(scope_mod.run_list_suite_clause(("Payments", "cart", "payments ")))
    assert sql.count(" IN (") == 2, sql
    assert "= %(" not in sql.split("WHERE", 1)[1].replace("test_cases.test_run_id = test_runs.id", ""), sql


def test_no_or_blank_suite_filters_nothing():
    assert scope_mod.run_list_suite_clause(None) is None
    assert scope_mod.run_list_suite_clause(()) is None
    assert scope_mod.run_list_suite_clause("   ") is None


# ── the service: tuples are OR, one value is the legacy statement ─────────────


class _Result:
    def scalar(self):
        return 0

    def all(self):
        return []


class _Capture:
    def __init__(self):
        self.sql: list[str] = []

    async def execute(self, stmt, *a, **k):
        self.sql.append(_sql(stmt))
        return _Result()


async def _runs_sql(**kw) -> str:
    from app.services import runs_service

    db = _Capture()
    await runs_service.list_project_runs(db, P, 1, 20, None, accessible_project_ids=None, days=30, **kw)
    return db.sql[0]  # the count statement: the shared filter list


@pytest.mark.asyncio
async def test_one_release_is_the_scalar_predicate_and_several_are_one_in():
    one = await _runs_sql(release_id=R1)
    assert "test_runs.primary_release_id = %(primary_release_id_1)s" in one
    assert " IN (" not in one.split("FROM", 2)[-1].split("projects")[-1]
    many = await _runs_sql(release_id=(R1, R2))
    assert "test_runs.primary_release_id IN (" in many
    mixed = await _runs_sql(release_id=(R1, UNATTRIBUTED))
    assert "test_runs.primary_release_id IN (" in mixed and "primary_release_id IS NULL" in mixed


@pytest.mark.asyncio
async def test_releases_and_suites_are_anded():
    sql = await _runs_sql(release_id=(R1, R2), suite_name=("payments", "cart"))
    where = sql.split("WHERE", 1)[1]
    assert "primary_release_id IN (" in where and "lower(trim(coalesce(test_runs.primary_suite_name" in where
    assert " AND " in where


# ── the resolver: none / one (legacy) / many (batched, every id) ──────────────


@pytest.mark.asyncio
async def test_no_release_costs_nothing():
    db = AsyncMock()
    assert await scope_mod.resolve_release_query_scope_list(db, object(), None) is None
    assert await scope_mod.resolve_release_query_scope_list(db, object(), []) is None
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_release_goes_through_the_legacy_single_resolver():
    single = AsyncMock(return_value=R1)
    batch = AsyncMock()
    with patch("app.core.deps.resolve_release_query_scope", single), \
         patch("app.core.deps.resolve_release_query_scopes", batch):
        got = await scope_mod.resolve_release_query_scope_list(AsyncMock(), "u", [R1])
    assert got == R1
    single.assert_awaited_once()
    batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_several_releases_are_each_authorised_in_one_batch():
    batch = AsyncMock(return_value=[R1, R2])
    with patch("app.core.deps.resolve_release_query_scopes", batch):
        got = await scope_mod.resolve_release_query_scope_list(
            AsyncMock(), "u", [R1, R2.upper(), R1]
        )
    assert got == (R1, R2)
    # Canonicalised and de-duplicated BEFORE authorisation, every id passed.
    assert list(batch.await_args.args[1]) == [R1, R2]


@pytest.mark.asyncio
async def test_a_forbidden_id_among_readable_ones_is_the_403():
    batch = AsyncMock(side_effect=HTTPException(403, "You do not have access to this release"))
    with patch("app.core.deps.resolve_release_query_scopes", batch):
        with pytest.raises(HTTPException) as exc:
            await scope_mod.resolve_release_query_scope_list(AsyncMock(), "u", [R1, R2])
    assert exc.value.status_code == 403


def test_suite_parse_is_c1():
    assert scope_mod.parse_suite_filter(None) is None
    assert scope_mod.parse_suite_filter(["a"]) == "a"
    assert scope_mod.parse_suite_filter(["a", "b", "a"]) == ("a", "b")


# ── over HTTP: caps and malformed values are the C1 body, before any query ────


@pytest.fixture
def client():
    """The two real routers on a bare app with the production exception
    handlers -- no auth middleware (it needs Redis), the user overridden."""
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.testclient import TestClient
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.core import analytics_errors as errors
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import my_failures, runs

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, errors.http_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_exception_handler)
    app.add_exception_handler(errors.AnalyticsQueryError, errors.analytics_query_error_handler)
    app.include_router(runs.router)
    app.include_router(my_failures.router)

    db = AsyncMock()

    async def _db():
        yield db

    user = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", is_active=True, api_key_project_id=None)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    yield TestClient(app, raise_server_exceptions=False), db


def test_the_production_app_renders_the_c1_error_on_every_route():
    """The handler the fixture installs is the one ``app.main`` installs
    app-wide, so these routes answer a C1 violation with the contract body
    without being marked (their other errors keep FastAPI's bodies)."""
    from app.core.analytics_errors import AnalyticsQueryError, analytics_query_error_handler
    from app.main import app

    assert app.exception_handlers[AnalyticsQueryError] is analytics_query_error_handler


ROUTES = ("/api/v1/runs", "/api/v1/me/assigned-failures", "/api/v1/me/assigned-failures/count")


def _ids(n: int) -> list[tuple[str, str]]:
    return [("release_id", str(uuid.UUID(int=i + 1))) for i in range(n)]


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize(
    "params, code, param",
    [
        (_ids(21), "release_cap", "release_id"),
        ([("suite_name", f"s{i}") for i in range(51)], "suite_cap", "suite_name"),
        ([("release_id", str(uuid.uuid4())), ("release_id", "nope")], "release_id_format", "release_id"),
        ([("suite_name", "a"), ("suite_name", "x" * 501)], "suite_name_length", "suite_name"),
    ],
)
def test_c1_violations_are_422_with_the_contract_body(client, path, params, code, param):
    http, db = client
    resp = http.get(path, params=params)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == code and body["param"] == param, body
    assert body["detail"] and "allowed" in body
    db.execute.assert_not_awaited()


@pytest.mark.parametrize("path", ROUTES)
def test_the_caps_admit_the_limit(client, path):
    """20 releases / 50 suites are allowed: the request reaches authorisation."""
    http, _db = client
    batch = AsyncMock(side_effect=HTTPException(404, "Release not found"))
    with patch("app.core.deps.resolve_release_query_scopes", batch):
        resp = http.get(path, params=_ids(20) + [("suite_name", f"s{i}") for i in range(50)])
    assert resp.status_code == 404, resp.text
    assert len(batch.await_args.args[1]) == 20


@pytest.mark.parametrize("path", ROUTES)
def test_every_id_reaches_the_authoriser_over_http(client, path):
    """Repeated keys arrive as a list (a scalar parameter would keep the last)."""
    http, _db = client
    batch = AsyncMock(side_effect=HTTPException(403, "You do not have access to this release"))
    with patch("app.core.deps.resolve_release_query_scopes", batch):
        resp = http.get(path, params=[("release_id", R1), ("release_id", R2)])
    assert resp.status_code == 403
    assert list(batch.await_args.args[1]) == [R1, R2]
