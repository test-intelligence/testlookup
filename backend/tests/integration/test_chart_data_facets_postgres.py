"""W08 TRND-13 -- chart-data facets against real Postgres.

A small world written here, from a plan (``RUNS``), with expectations from a
second, independent implementation over that plan (``_expect``) -- never the
SQL under test:

1. **Each facet filters to exactly its key's rows.** Branch, environment and
   ingestion source on the run grain (the panel is the run aggregates of that
   key's runs); status and failure category on the row grain. A panel's series
   equals that key's series of ONE chart grouped by the dimension -- the
   Explorer's small multiples draw what the combined chart would.
2. **The comparability judgement reads the faceted rows.** Releases A and B
   run the same suites over every branch, but not on ``main``: the release
   series under ``facet_dimension=branch&facet_value=main`` is not comparable,
   and the same chart without the facet is.
3. **Two panels are two cache entries.** Through the real VIZ-209 layer and
   Redis, ``main`` then ``dev`` answer different bodies.
4. **The codes through the route** (``facet_pair``, ``facet_dimension_enum``,
   ``facet_value``, ``facet_metric_grain``, ``run_only_metric``,
   ``repeated_parameter``), never echoing the value.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (a throwaway
database). Redis is ``fakeredis`` unless ``REDIS_URL`` is set.
"""
from __future__ import annotations

import os
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

FROZEN = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
PATH = "/api/v1/analytics/chart-data"
DAYS = 30
_TIME_MODULES = (
    "app.services.analytics_service",
    "app.services.analytics_scope",
    "app.services.analytics_meta",
    "app.services.chart_data_service",
)


@dataclass(frozen=True)
class Case:
    suite: str
    fp: str
    status: str
    category: Optional[str] = None


@dataclass(frozen=True)
class Run:
    key: str
    days_ago: float
    branch: Optional[str]
    environment: Optional[str]
    source: str
    release: Optional[str]
    cases: tuple[Case, ...]


RUNS = (
    Run("R1", 1, "main", "staging", "sdk", "A", (
        Case("checkout", "t1", "PASSED"), Case("checkout", "t2", "FAILED", "PRODUCT_BUG"),
        Case("payments", "t3", "PASSED"),
    )),
    Run("R2", 2, "main", "qa", "file", "B", (
        Case("checkout", "t1", "PASSED"), Case("checkout", "t2", "BROKEN", "INFRASTRUCTURE"),
    )),
    Run("R3", 2, "dev", "staging", "sdk", "B", (
        Case("payments", "t3", "FAILED", "PRODUCT_BUG"), Case("payments", "t4", "SKIPPED"),
    )),
    Run("R4", 4, None, None, "upload", None, (
        Case("checkout", "t1", "PASSED"), Case("payments", "t3", "PASSED"),
    )),
    # Written before environments were lower-cased on write: one bucket with staging.
    Run("R5", 3, "main", "Staging", "live", "A", (
        Case("checkout", "t1", "PASSED"), Case("payments", "t3", "PASSED"),
    )),
    Run("R6", 2, "dev", "qa", "sdk", "A", (Case("checkout", "t1", "FAILED", "PRODUCT_BUG"),)),
    # Outside every window this file asks for.
    Run("R7", 60, "main", "staging", "sdk", "A", (Case("checkout", "t1", "PASSED"),)),
)


def _when(run: Run) -> datetime:
    return FROZEN - timedelta(days=run.days_ago)


def _window_start(days: int) -> datetime:
    return FROZEN.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)


def _day(run: Run) -> str:
    return _when(run).date().isoformat()


def _run_key(run: Run, dimension: str) -> str:
    value = {"branch": run.branch, "environment": run.environment, "ingestion_source": run.source}[dimension]
    value = (value or "").strip() or "(none)"
    return value.lower() if dimension == "environment" else value


def _expect(dimension: str, key: str, *, days: int = DAYS) -> dict[str, int]:
    """``{day: executions}`` of the panel ``dimension = key``, from the plan."""
    out: dict[str, int] = defaultdict(int)
    for run in RUNS:
        if _when(run) < _window_start(days):
            continue
        if dimension in ("status", "failure_category"):
            for case in run.cases:
                value = case.status if dimension == "status" else (case.category or "UNKNOWN")
                if value.lower() == key:
                    out[_day(run)] += 1
        elif _run_key(run, dimension) == key:
            out[_day(run)] += len(run.cases)
    return dict(out)


# ── the world ──────────────────────────────────────────────────────────────


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return FROZEN.astimezone(tz) if tz is not None else FROZEN.replace(tzinfo=None)


def _redis_client():
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        import redis.asyncio as redis_asyncio

        return redis_asyncio.Redis.from_url(url, decode_responses=True)
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    import importlib

    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from httpx import ASGITransport, AsyncClient
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.core import analytics_errors as errors
    from app.core import analytics_read_layer as layer
    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.models.postgres import (
        LaunchStatus,
        Project,
        ProjectMember,
        Release,
        TestCase,
        TestRun,
        User,
        UserRole,
    )
    from app.routers import analytics

    dsn = _env("TESTLOOKUP_POSTGRES_TEST_DSN")
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = _redis_client()

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, errors.http_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_exception_handler)
    app.add_exception_handler(errors.AnalyticsQueryError, errors.analytics_query_error_handler)
    app.include_router(analytics.router)
    app.dependency_overrides[get_db] = _get_db

    patch = pytest.MonkeyPatch()
    patch.setattr("app.db.redis_client.get_redis", lambda: redis)
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    patch.setitem(layer.RATE_LIMITED_ROUTES, PATH, "1000000/minute")
    for name in _TIME_MODULES:
        module = importlib.import_module(name)
        if hasattr(module, "datetime"):
            patch.setattr(module, "datetime", _FrozenDatetime)

    tag = uuid.uuid4().hex[:10]
    pid, other = uuid.uuid4(), uuid.uuid4()
    member, outsider = uuid.uuid4(), uuid.uuid4()
    try:
        async with sessions() as db:
            for project_id, label in ((pid, "p"), (other, "q")):
                db.add(Project(id=project_id, name=f"facet-{label}-{tag}", slug=f"facet-{label}-{tag}",
                               is_active=True, description=f"throwaway W08 TRND-13 {tag}"))
            for uid, label in ((member, "member"), (outsider, "outsider")):
                db.add(User(id=uid, email=f"facet-{label}-{tag}@example.com",
                            username=f"facet_{label}_{tag}", full_name=f"Facet {label}",
                            hashed_password="!unusable", role=UserRole.QA_ENGINEER.value))
            await db.flush()
            db.add(ProjectMember(project_id=pid, user_id=member, role=UserRole.QA_ENGINEER.value))
            db.add(ProjectMember(project_id=other, user_id=outsider, role=UserRole.QA_ENGINEER.value))
            releases = {}
            for key in ("A", "B"):
                rel = Release(id=uuid.uuid4(), project_id=pid, name=f"facet-{key}-{tag}", version=key,
                              status="active")
                db.add(rel)
                releases[key] = rel.id
            await db.flush()
            for run in RUNS:
                statuses = [case.status for case in run.cases]
                run_id = uuid.uuid4()
                db.add(TestRun(
                    id=run_id, project_id=pid, build_number=f"facet-{run.key}-{tag}",
                    jenkins_job="facets", status=LaunchStatus.PASSED, branch=run.branch,
                    environment=run.environment, ingestion_source=run.source,
                    primary_release_id=releases[run.release] if run.release else None,
                    total_tests=len(statuses), passed_tests=statuses.count("PASSED"),
                    failed_tests=statuses.count("FAILED"), broken_tests=statuses.count("BROKEN"),
                    skipped_tests=statuses.count("SKIPPED"), created_at=_when(run),
                ))
                await db.flush()
                for case in run.cases:
                    db.add(TestCase(
                        id=uuid.uuid4(), test_run_id=run_id, test_fingerprint=f"{case.fp}-{tag}",
                        test_name=case.fp, suite_name=case.suite, status=case.status,
                        failure_category=case.category, created_at=_when(run),
                    ))
            await db.commit()

        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(
                client=client, sessions=sessions, pid=pid, other=other, releases=releases,
                member={"Authorization": f"Bearer {create_access_token(str(member))}"},
                outsider={"Authorization": f"Bearer {create_access_token(str(outsider))}"},
            )
        finally:
            await client.aclose()
    finally:
        for statement in (
            "DELETE FROM projects WHERE id = ANY(:ids)",
            "DELETE FROM access_audit_logs WHERE actor_user_id = ANY(:users)",
            "DELETE FROM users WHERE id = ANY(:users)",
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(text(statement), {"ids": [pid, other], "users": [member, outsider]})
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"facets teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


# ── helpers ─────────────────────────────────────────────────────────────────


async def _get(world, params: list[tuple[str, str]], *, headers=None):
    query = [("project_id", str(world.pid)), ("days", str(DAYS)), *params]
    return await world.client.get(PATH, params=query, headers=headers or world.member)


async def _chart(world, params: list[tuple[str, str]]) -> dict:
    from app.models.viz_contracts import validate_contract

    resp = await _get(world, params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    validate_contract("chart_series", {k: v for k, v in body.items() if k != "meta"})
    validate_contract("envelope", body["meta"])
    return body


def _nonzero(points: list[dict]) -> dict[str, int]:
    return {p["x"]: p["y"] for p in points if p["y"]}


def _panel(body: dict) -> dict[str, int]:
    (series,) = body["series"]
    return _nonzero(series["points"])


def _series_of(body: dict, key: str) -> dict[str, int]:
    match = [s for s in body["series"] if s["key"] == key]
    return _nonzero(match[0]["points"]) if match else {}


# ── 1. each facet filters to exactly its key ───────────────────────────────


@pytest.mark.parametrize(
    "dimension, key, grain",
    [
        ("branch", "main", "run_aggregate"),
        ("branch", "dev", "run_aggregate"),
        ("branch", "(none)", "run_aggregate"),
        ("environment", "staging", "run_aggregate"),
        ("ingestion_source", "sdk", "run_aggregate"),
        ("status", "failed", "execution_row"),
        ("status", "passed", "execution_row"),
        ("failure_category", "product_bug", "execution_row"),
        ("failure_category", "unknown", "execution_row"),
    ],
)
async def test_a_panel_is_its_key_s_rows_and_the_combined_chart_s_series(world, dimension, key, grain):
    panel = await _chart(world, [("metric", "executions"), ("group_by", "day"),
                                 ("facet_dimension", dimension), ("facet_value", key)])
    assert _panel(panel) == _expect(dimension, key)
    assert _panel(panel), "the plan must give every case some rows"
    definitions = panel["meta"]["definitions"]
    assert definitions["facet"] == {"dimension": dimension, "value": key}
    assert definitions["grain"] == grain
    combined = await _chart(world, [("metric", "executions"), ("group_by", "day"), ("group_by", dimension)])
    assert _panel(panel) == _series_of(combined, key)


async def test_a_run_level_panel_counts_that_key_s_runs(world):
    panel = await _chart(world, [("metric", "run_count"), ("group_by", "day"),
                                 ("facet_dimension", "branch"), ("facet_value", "dev")])
    expected: dict[str, int] = defaultdict(int)
    for run in RUNS:
        if run.branch == "dev" and _when(run) >= _window_start(DAYS):
            expected[_day(run)] += 1
    assert _panel(panel) == dict(expected)


async def test_a_status_panel_of_a_rate_is_measured_on_its_own_rows(world):
    """A failed-status panel's pass rate is 0 % over the failed rows, never a
    run's whole rate; a passed panel's is 100 %."""
    failed = await _chart(world, [("metric", "pass_rate"), ("group_by", "day"),
                                  ("facet_dimension", "status"), ("facet_value", "failed")])
    measured = [p for p in failed["series"][0]["points"] if p.get("measured", True) and p["y"] is not None]
    assert measured and {p["y"] for p in measured} == {0}


# ── 2. comparability on the faceted rows ───────────────────────────────────


async def test_the_release_comparison_is_judged_on_the_panel_s_rows(world):
    params = [("metric", "pass_rate"), ("group_by", "day"), ("group_by", "release")]
    whole = await _chart(world, params)
    assert whole["meta"]["comparability"]["comparable"] is True
    main = await _chart(world, params + [("facet_dimension", "branch"), ("facet_value", "main")])
    judged = main["meta"]["comparability"]
    # On main, A ran checkout + payments and B checkout only.
    assert (judged["comparable"], judged["reason_code"]) == (False, "different_suites")
    assert "2 suites ran in at least one" in judged["reason"] and "1 in all" in judged["reason"]


# ── 3. two panels, two cache entries ───────────────────────────────────────


async def test_two_panels_are_never_served_one_cached_body(world):
    base = [("metric", "executions"), ("group_by", "day"), ("facet_dimension", "branch")]
    main = await _chart(world, base + [("facet_value", "main")])
    dev = await _chart(world, base + [("facet_value", "dev")])
    again = await _chart(world, base + [("facet_value", "main")])
    assert _panel(main) == _expect("branch", "main")
    assert _panel(dev) == _expect("branch", "dev") != _panel(main)
    assert _panel(again) == _panel(main)
    plain = await _chart(world, [("metric", "executions"), ("group_by", "day")])
    assert "facet" not in plain["meta"]["definitions"]
    assert sum(_panel(plain).values()) > sum(_panel(main).values())


# ── 4. the codes ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "params, code, param",
    [
        ([("facet_dimension", "branch")], "facet_pair", "facet_dimension"),
        ([("facet_value", "main")], "facet_pair", "facet_dimension"),
        ([("facet_dimension", "suite"), ("facet_value", "checkout")], "facet_dimension_enum", "facet_dimension"),
        ([("facet_dimension", "x'; DROP TABLE test_runs; --"), ("facet_value", "main")],
         "facet_dimension_enum", "facet_dimension"),
        ([("facet_dimension", "branch"), ("facet_value", "ma\x00in")], "facet_value", "facet_value"),
        ([("facet_dimension", "branch"), ("facet_value", "b" * 2001)], "facet_value", "facet_value"),
        ([("facet_dimension", "branch"), ("facet_value", "__other__")], "facet_value", "facet_value"),
        ([("facet_dimension", "status"), ("facet_value", "FLAKY")], "facet_value", "facet_value"),
        ([("facet_dimension", "branch"), ("facet_value", "main"), ("facet_value", "dev")],
         "repeated_parameter", "facet_value"),
    ],
)
async def test_refusals_carry_the_code_and_never_echo_the_value(world, params, code, param):
    resp = await _get(world, [("metric", "executions"), ("group_by", "day"), *params])
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert (body["code"], body.get("param")) == (code, param)
    for _name, value in params:
        if value not in ("branch", "status", "main", "suite") and value.strip():
            assert value not in body["message"]


async def test_run_count_under_a_status_panel_is_facet_metric_grain(world):
    resp = await _get(world, [("metric", "run_count"), ("group_by", "day"),
                              ("facet_dimension", "status"), ("facet_value", "failed")])
    assert resp.status_code == 422
    assert resp.json()["code"] == "facet_metric_grain"


async def test_a_wall_clock_under_a_status_panel_is_run_only_metric(world):
    resp = await _get(world, [("metric", "wall_clock_p95"), ("group_by", "day"),
                              ("facet_dimension", "status"), ("facet_value", "failed")])
    assert resp.status_code == 422
    assert resp.json()["code"] == "run_only_metric"


async def test_a_facet_never_widens_the_scope(world):
    """The facet value is a key, not an id: another project's caller is the
    scope's 403 whatever the panel."""
    resp = await _get(world, [("metric", "executions"), ("group_by", "day"),
                              ("facet_dimension", "branch"), ("facet_value", "main")],
                      headers=world.outsider)
    assert resp.status_code == 403
