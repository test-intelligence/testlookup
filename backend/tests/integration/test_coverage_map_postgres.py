"""VIZ-206 -- ``GET /api/v1/analytics/coverage-map`` against real Postgres.

The world is written here, from a small declarative plan (``P1`` below), and the
expectations come from ``_expect``: a second, independent implementation of the
map in plain Python over that PLAN -- never the SQL under test and never a
number captured from a previous run. The headline case also asserts literal
numbers a human counted, so a matching pair of wrong implementations cannot
pass.

What only a database can prove:

1. **The two populations.** Executed tests under their effective suite (a
   live-stream run's label, ``(none)`` for rows with no suite, two spellings of
   one suite as one key); idle canonical tests under their canonical suite,
   with recency from ``last_seen_run_id`` -- ``seen`` with the run's date,
   ``unknown`` once a run deletion cleared the link (the real ``SET NULL``
   foreign key), ``never`` only for a case nothing ever executed; ``deleted``
   canonicals excluded, ``needs_review`` included.
2. **Every level partitions its parent.** Walked from the real ids, every
   level's children sum to the parent's ``value`` and the level's root equals
   the child it was opened from.
3. **The envelope and the layer.** Every body validates as C3 ``tree`` and its
   ``meta`` as C2; authz (member, outsider, admin, no project); the 422s; the
   499-child cap with one ``Other (n)``; the cache answers a repeat from Redis
   and a canonical move and a suite rename, through their real routes,
   invalidate it (the VIZ-212 bumps this story added); a retired case leaves
   the map.
4. **Reconciliation with /analytics/coverage** where the two must agree, and the
   documented difference where they must not.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (a throwaway
database). Redis is ``fakeredis`` unless ``REDIS_URL`` is set. The router is
mounted on a bare app here (registration in ``app.bootstrap`` is the
integrator's), with the production exception handlers.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
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
PATH = "/api/v1/analytics/coverage-map"
SEP = "␟"
HOSTILE_SUITE = "<img src=x onerror=alert(1)>"
_TIME_MODULES = (
    "app.services.analytics_service",
    "app.services.analytics_scope",
    "app.services.analytics_meta",
    "app.services.chart_data_service",
    "app.services.coverage_map_service",
)


# ── the plan: what the world holds ─────────────────────────────────────────


@dataclass(frozen=True)
class Run:
    key: str
    days_ago: float
    trigger_source: Optional[str] = None
    primary_suite: Optional[str] = None
    release: Optional[str] = None
    in_progress: bool = False
    deleted: bool = False  # written, then deleted: its rows and canonical links go


@dataclass(frozen=True)
class Row:
    run: str
    fp: str
    name: str
    suite: Optional[str]
    cls: Optional[str]
    status: str  # TestStatus value
    flaky: bool = False


@dataclass(frozen=True)
class Canon:
    fp: str
    name: str
    cls: Optional[str]
    suite: str
    status: str = "active"
    source: str = "execution"
    first: Optional[str] = None
    last: Optional[str] = None


@dataclass
class Plan:
    suites: list[str]
    runs: list[Run]
    rows: list[Row]
    canons: list[Canon]
    releases: list[str] = field(default_factory=list)


P1 = Plan(
    suites=["Payments", "Orders", "Legacy", "Default Suite"],
    releases=["R1"],
    runs=[
        Run("RA", 1, release="R1", in_progress=True),
        Run("RB", 5),
        Run("RL", 2, trigger_source="live_stream", primary_suite="Payments"),
        Run("RO", 40),
        Run("RX", 50, deleted=True),
    ],
    rows=[
        Row("RA", "fp-01", "test_pay_ok", "Payments", "tests/api/test_pay.py", "PASSED"),
        Row("RA", "fp-02", "test_pay_fail", "Payments", "tests/api/test_pay.py", "FAILED"),
        Row("RA", "fp-03", "test_refund", "payments", None, "PASSED", flaky=True),
        Row("RA", "fp-04", "test_order_skip", "Orders", "com.acme.OrderTest", "SKIPPED"),
        Row("RA", "fp-05", "test_nosuite", None, "  ", "PASSED"),
        Row("RB", "fp-01", "test_pay_ok", "Payments", "tests/api/test_pay.py", "PASSED"),
        Row("RB", "fp-02", "test_pay_fail", "Payments", "tests/api/test_pay.py", "BROKEN"),
        Row("RB", "fp-03", "test_refund", "payments", None, "PASSED"),
        Row("RB", "fp-04", "test_order_skip", "Orders", "com.acme.OrderTest", "SKIPPED"),
        Row("RB", "fp-12", "test_moved", "Orders", "com.acme.OrderTest", "PASSED"),
        # The SDK stamps the class name as the row's suite; the run's label wins.
        Row("RL", "fp-06", "test_live", "com.acme.LiveTest", "com.acme.LiveTest", "PASSED"),
        Row("RO", "fp-07", "test_old", "Orders", "com.acme.OldTest", "PASSED"),
        Row("RX", "fp-08", "test_lost", "Legacy", "legacy.A", "PASSED"),
    ],
    canons=[
        Canon("fp-01", "test_pay_ok", "tests/api/test_pay.py", "Payments", first="RB", last="RA"),
        Canon("fp-02", "test_pay_fail", "tests/api/test_pay.py", "Payments", first="RB", last="RA"),
        Canon("fp-03", "test_refund", None, "Payments", first="RB", last="RA"),
        Canon("fp-04", "test_order_skip", "com.acme.OrderTest", "Orders", first="RB", last="RA"),
        Canon("fp-05", "test_nosuite", None, "Default Suite", first="RA", last="RA"),
        Canon("fp-06", "test_live", "com.acme.LiveTest", "Payments", first="RL", last="RL"),
        Canon("fp-07", "test_old", "com.acme.OldTest", "Orders", first="RO", last="RO"),
        # Its only run is deleted: both links are SET NULL and its rows go.
        Canon("fp-08", "test_lost", "legacy.A", "Legacy", first="RX", last="RX"),
        # Authored, never executed.
        Canon("fp-09", "test_planned", None, "Legacy", source="managed"),
        Canon("fp-10", "test_retired", "x", "Payments", status="deleted", first="RO", last="RO"),
        Canon("fp-11", "test_review", "legacy.A", "Legacy", status="needs_review",
              first="RO", last="RO"),
        # Manually moved to Payments; it still RUNS under Orders.
        Canon("fp-12", "test_moved", "com.acme.OrderTest", "Payments", first="RB", last="RB"),
    ],
)

P3 = Plan(
    suites=["Alpha", "Beta", HOSTILE_SUITE],
    runs=[Run("RP", 1), Run("RP2", 2)],
    rows=[
        Row("RP", "fp-h1", "constructor", HOSTILE_SUITE, "__proto__", "PASSED"),
        Row("RP", "fp-dual", "test_dual", "Alpha", "a.Dual", "PASSED"),
        Row("RP2", "fp-dual", "test_dual", "Beta", "a.Dual", "FAILED"),
    ],
    canons=[
        Canon("fp-h1", "constructor", "__proto__", HOSTILE_SUITE, first="RP", last="RP"),
        Canon("fp-dual", "test_dual", "a.Dual", "Alpha", first="RP2", last="RP"),
        Canon("fp-idle", "test_idle", None, "Alpha", status="needs_review", source="managed"),
        Canon("fp-retire", "test_retire", "b.Gone", "Beta", first="RP2", last="RP2"),
    ],
)


def _when(run: Run) -> datetime:
    return FROZEN - timedelta(days=run.days_ago)


# ── the independent reducer ────────────────────────────────────────────────


def _window_start(days: int) -> datetime:
    return FROZEN.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)


def _effective(run: Run, row: Row) -> Optional[str]:
    if run.trigger_source == "live_stream" and (run.primary_suite or "").strip():
        return run.primary_suite.strip()
    return (row.suite or "").strip() or None


def _cls(value: Optional[str]) -> str:
    return (value or "").strip() or "__none__"


def _tests(plan: Plan, days: int, release: Optional[str] = None) -> list[dict]:
    """One dict per (suite key, fingerprint) placed in the map."""
    runs = {run.key: run for run in plan.runs if not run.deleted}
    start = _window_start(days)
    placed: dict[tuple, dict] = {}
    seen: set[str] = set()
    for row in plan.rows:
        run = runs.get(row.run)
        if run is None or _when(run) < start or (release and run.release != release):
            continue
        seen.add(row.fp)
        suite = (_effective(run, row) or "(none)").lower()
        test = placed.setdefault((suite, row.fp), {
            "suite": suite, "cls": _cls(row.cls), "fp": row.fp, "name": row.name,
            "executions": 0, "passed": 0, "failed": 0, "broken": 0,
            "flaky": False, "last": None, "lost": False,
        })
        test["executions"] += 1
        if row.status in ("PASSED", "FAILED", "BROKEN"):
            test[row.status.lower()] += 1
        test["flaky"] = test["flaky"] or row.flaky
        test["last"] = max(filter(None, (test["last"], _when(run))))
    for canon in plan.canons:
        if canon.status == "deleted" or canon.fp in seen:
            continue
        last_run = runs.get(canon.last) if canon.last else None
        first_run = runs.get(canon.first) if canon.first else None
        has_rows = any(r.fp == canon.fp and r.run in runs for r in plan.rows)
        placed[(canon.suite.strip().lower(), canon.fp)] = {
            "suite": canon.suite.strip().lower(), "cls": _cls(canon.cls), "fp": canon.fp,
            "name": canon.name, "executions": 0, "passed": 0, "failed": 0, "broken": 0,
            "flaky": False, "last": _when(last_run) if last_run else None,
            "lost": last_run is None and (
                first_run is not None or canon.source in ("execution", "linked") or has_rows
            ),
        }
    return list(placed.values())


def _stats(tests: list[dict]) -> dict:
    n = len(tests)
    passed = sum(t["passed"] for t in tests)
    evaluated = passed + sum(t["failed"] + t["broken"] for t in tests)
    flaky = sum(1 for t in tests if t["flaky"])
    last = max((t["last"] for t in tests if t["last"]), default=None)
    if last is not None:
        recency = "seen"
    elif any(t["lost"] for t in tests):
        recency = "unknown"
    else:
        recency = "never"
    return {
        "test_count": n,
        "executions": sum(t["executions"] for t in tests),
        "pass_rate": round(passed / evaluated * 100, 2) if evaluated else None,
        "flaky_count": flaky,
        "flaky_share": round(flaky / n, 4) if n else None,
        "last_executed_at": last.isoformat() if last else None,
        "staleness_days": (FROZEN.date() - last.date()).days if last else None,
        "recency": recency,
    }


def _expect(plan: Plan, days: int, depth: int, suite=None, cls=None, release=None) -> dict:
    """``{child id: stats}`` plus ``"root"`` for one level."""
    tests = _tests(plan, days, release)
    if depth >= 2:
        tests = [t for t in tests if t["suite"] == suite]
    if depth == 3:
        tests = [t for t in tests if t["cls"] == cls]
    groups: dict[str, list[dict]] = {}
    for t in tests:
        if depth == 1:
            child = f"s:{t['suite']}"
        elif depth == 2:
            child = f"c:{suite}{SEP}{t['cls']}"
        else:
            child = f"t:{t['fp']}"
        groups.setdefault(child, []).append(t)
    out = {child: _stats(group) for child, group in groups.items()}
    if tests:
        out["root"] = _stats(tests)
    return out


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


async def _write_plan(db, project, plan: Plan, tag: str) -> dict:
    from app.models.postgres import (
        CanonicalTestCase,
        LaunchStatus,
        Release,
        TestCase,
        TestRun,
        TestSuite,
    )

    suites = {}
    for name in plan.suites:
        suite = TestSuite(
            id=uuid.uuid4(), project_id=project.id, name=name,
            is_default=name == "Default Suite",
        )
        db.add(suite)
        suites[name] = suite
    releases = {}
    for key in plan.releases:
        rel = Release(
            id=uuid.uuid4(), project_id=project.id, name=f"cm-{key}-{tag}", version=key,
            status="active",
        )
        db.add(rel)
        releases[key] = rel.id
    await db.flush()
    runs = {}
    for run in plan.runs:
        mine = [row for row in plan.rows if row.run == run.key]
        row_obj = TestRun(
            id=uuid.uuid4(), project_id=project.id, build_number=f"cm-{run.key}-{tag}",
            jenkins_job="coverage-map",
            status=LaunchStatus.IN_PROGRESS if run.in_progress else LaunchStatus.PASSED,
            ingestion_source="unknown", trigger_source=run.trigger_source,
            primary_suite_name=run.primary_suite,
            primary_release_id=releases.get(run.release) if run.release else None,
            total_tests=len(mine),
            passed_tests=sum(r.status == "PASSED" for r in mine),
            failed_tests=sum(r.status == "FAILED" for r in mine),
            broken_tests=sum(r.status == "BROKEN" for r in mine),
            skipped_tests=sum(r.status == "SKIPPED" for r in mine),
            created_at=_when(run),
        )
        db.add(row_obj)
        runs[run.key] = row_obj.id
    await db.flush()
    for row in plan.rows:
        db.add(TestCase(
            id=uuid.uuid4(), test_run_id=runs[row.run], test_fingerprint=row.fp,
            test_name=row.name, suite_name=row.suite, class_name=row.cls, status=row.status,
            is_flaky_run=row.flaky,
            created_at=_when(next(r for r in plan.runs if r.key == row.run)),
        ))
    canonicals = {}
    for canon in plan.canons:
        obj = CanonicalTestCase(
            id=uuid.uuid4(), project_id=project.id, test_suite_id=suites[canon.suite].id,
            test_fingerprint=canon.fp, test_name=canon.name, class_name=canon.cls,
            status=canon.status, source=canon.source,
            first_seen_run_id=runs[canon.first] if canon.first else None,
            last_seen_run_id=runs[canon.last] if canon.last else None,
        )
        db.add(obj)
        canonicals[canon.fp] = obj.id
    await db.flush()
    for run in plan.runs:
        if run.deleted:
            await db.execute(text("DELETE FROM test_runs WHERE id = :id"), {"id": runs[run.key]})
    return {"suites": {k: v.id for k, v in suites.items()}, "runs": runs,
            "releases": releases, "canonicals": canonicals}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
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
        CanonicalTestCase,
        Project,
        ProjectMember,
        TestSuite,
        User,
        UserRole,
    )
    from app.routers import analytics_coverage_map, suites

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
    app.include_router(analytics_coverage_map.router)
    # The canonical move and suite rename routes, for the cache-invalidation tests.
    app.include_router(suites.router)
    app.dependency_overrides[get_db] = _get_db

    patch = pytest.MonkeyPatch()
    patch.setattr("app.db.redis_client.get_redis", lambda: redis)
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    patch.setitem(layer.RATE_LIMITED_ROUTES, PATH, "1000000/minute")
    import importlib

    for name in _TIME_MODULES:
        module = importlib.import_module(name)
        if hasattr(module, "datetime"):
            patch.setattr(module, "datetime", _FrozenDatetime)

    tag = uuid.uuid4().hex[:10]
    ids = {name: uuid.uuid4() for name in ("p1", "p2", "p3", "p4", "pd")}
    admin, member, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        async with sessions() as db:
            projects = {}
            for name, pid in ids.items():
                projects[name] = Project(
                    id=pid, name=f"cmap-{name}-{tag}", slug=f"cmap-{name}-{tag}",
                    is_active=name != "pd", description=f"throwaway VIZ-206 {tag}",
                )
                db.add(projects[name])
            for uid, label, role in (
                (admin, "admin", UserRole.ADMIN),
                (member, "member", UserRole.QA_ENGINEER),
                (outsider, "outsider", UserRole.QA_ENGINEER),
            ):
                db.add(User(
                    id=uid, email=f"cmap-{label}-{tag}@example.com",
                    username=f"cmap_{label}_{tag}", full_name=f"CMap {label}",
                    hashed_password="!unusable", role=role.value,
                ))
            await db.flush()
            for pid in (ids["p1"], ids["p3"]):
                db.add(ProjectMember(project_id=pid, user_id=member,
                                     role=UserRole.QA_ENGINEER.value))
            db.add(ProjectMember(project_id=ids["p2"], user_id=outsider,
                                 role=UserRole.QA_ENGINEER.value))
            p1 = await _write_plan(db, projects["p1"], P1, tag)
            p3 = await _write_plan(db, projects["p3"], P3, tag)
            # A deactivated project with data the map must never read.
            await _write_plan(db, projects["pd"], P3, tag + "d")
            # P4: 501 suites, one idle test each -- the 499-child cap.
            cap_suites = [
                TestSuite(id=uuid.uuid4(), project_id=ids["p4"], name=f"s{index:03d}")
                for index in range(501)
            ]
            db.add_all(cap_suites)
            await db.flush()
            db.add_all([
                CanonicalTestCase(
                    id=uuid.uuid4(), project_id=ids["p4"], test_suite_id=suite.id,
                    test_fingerprint=f"cap-{index:03d}", test_name=f"t{index}",
                    status="active", source="managed",
                )
                for index, suite in enumerate(cap_suites)
            ])
            await db.commit()

        def _jwt(uid):
            return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(
                client=client, sessions=sessions, redis=redis, tag=tag, ids=ids,
                p1=p1, p3=p3,
                admin=_jwt(admin), member=_jwt(member), outsider=_jwt(outsider),
            )
        finally:
            await client.aclose()
    finally:
        for statement in (
            "DELETE FROM canonical_test_cases WHERE project_id = ANY(:ids)",
            "DELETE FROM projects WHERE id = ANY(:ids)",
            "DELETE FROM access_audit_logs WHERE actor_user_id = ANY(:users)",
            "DELETE FROM users WHERE id = ANY(:users)",
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(text(statement), {
                        "ids": list(ids.values()), "users": [admin, member, outsider],
                    })
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"coverage-map teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


# ── helpers ─────────────────────────────────────────────────────────────────


async def _get(world, project: str, *, headers=None, expect: int = 200, **params):
    from app.models.viz_contracts import validate_contract

    query = [("project_id", str(world.ids[project]))] if project else []
    for name, value in params.items():
        for item in value if isinstance(value, list) else [value]:
            query.append((name, str(item)))
    resp = await world.client.get(PATH, params=query, headers=headers or world.admin)
    assert resp.status_code == expect, resp.text
    body = resp.json()
    if expect == 200:
        validate_contract("chart_series", {"kind": body["kind"], "nodes": body["nodes"]})
        validate_contract("envelope", body["meta"])
    return resp, body


def _by_id(body) -> dict:
    return {node["id"]: node for node in body["nodes"]}


def _actual(body) -> dict:
    """``{child id: stats}`` plus ``"root"``, in the reducer's shape."""
    out = {}
    for node in body["nodes"]:
        out["root" if node["parent_id"] is None else node["id"]] = node["stats"]
    return out


async def _leaves(world, project: str, days: int) -> list[tuple[str, dict]]:
    """Every test node of a project, ``(suite key, node)``, walked from the ids."""
    out = []
    _, level1 = await _get(world, project, depth=1, days=days)
    for suite_node in level1["nodes"][1:]:
        suite = _key(suite_node["id"], "s:")
        _, level2 = await _get(world, project, depth=2, suite=suite, days=days)
        for class_node in level2["nodes"][1:]:
            cls = _key(class_node["id"], f"c:{suite}{SEP}")
            _, level3 = await _get(
                world, project, depth=3, suite=suite, class_key=cls, days=days
            )
            out.extend((suite, node) for node in level3["nodes"][1:])
    return out


def _key(node_id: str, prefix: str) -> str:
    assert node_id.startswith(prefix), (node_id, prefix)
    return node_id[len(prefix):]


# ── 1. the headline, counted by hand ────────────────────────────────────────


async def test_the_headline_numbers_are_the_ones_a_human_counted(world):
    _, body = await _get(world, "p1", depth=1, days=30)
    nodes = body["nodes"]
    assert [n["id"] for n in nodes] == [
        "all", "s:payments", "s:legacy", "s:orders", "s:(none)",
    ]
    root, pay, legacy, orders, none = nodes
    assert (root["label"], root["value"], root["measure"]) == ("All suites", 11, 77.78)
    assert root["stats"]["flaky_share"] == 0.0909
    # Two spellings of one suite are one node; the label is one of them.
    assert pay["label"] in ("Payments", "payments")
    assert (pay["value"], pay["stats"]["executions"], pay["measure"]) == (4, 7, 71.43)
    assert (pay["stats"]["flaky_count"], pay["stats"]["flaky_share"]) == (1, 0.25)
    assert pay["stats"]["staleness_days"] == 1
    # Legacy ran nothing in the window: present, null rate, its last sighting 40 days ago.
    assert (legacy["value"], legacy["stats"]["executions"], legacy["measure"]) == (3, 0, None)
    assert legacy["stats"]["recency"] == "seen"
    assert legacy["stats"]["last_executed_at"] == "2026-08-12T12:00:00+00:00"
    assert legacy["stats"]["staleness_days"] == 40
    assert (orders["value"], orders["stats"]["executions"], orders["measure"]) == (3, 3, 100.0)
    assert (none["label"], none["value"]) == ("(none)", 1)
    assert sum(n["value"] for n in nodes[1:]) == root["value"]
    assert body["meta"]["truncated"] is False
    assert body["meta"]["definitions"]["coverage"] == "test_execution"
    assert body["meta"]["includes_in_progress"] >= 1


# ── 2. the independent reducer, every level ────────────────────────────────


@pytest.mark.parametrize("days", [30, 90])
async def test_every_level_matches_the_independent_reducer(world, days):
    """Walk the whole tree from the real ids; compare every node's stats."""
    _, level1 = await _get(world, "p1", depth=1, days=days)
    assert _actual(level1) == _expect(P1, days, 1)
    for suite_node in level1["nodes"][1:]:
        suite = _key(suite_node["id"], "s:")
        _, level2 = await _get(world, "p1", depth=2, suite=suite, days=days)
        assert _actual(level2) == _expect(P1, days, 2, suite=suite)
        assert level2["nodes"][0]["stats"] == suite_node["stats"]
        assert level2["nodes"][0]["label"] == suite_node["label"]
        for class_node in level2["nodes"][1:]:
            cls = _key(class_node["id"], f"c:{suite}{SEP}")
            _, level3 = await _get(world, "p1", depth=3, suite=suite, class_key=cls, days=days)
            assert _actual(level3) == _expect(P1, days, 3, suite=suite, cls=cls)
            assert level3["nodes"][0]["stats"] == class_node["stats"]
            assert sum(n["value"] for n in level3["nodes"][1:]) == class_node["value"]


async def test_a_release_scope_counts_its_runs_and_calls_the_rest_idle(world):
    release = str(world.p1["releases"]["R1"])
    _, body = await _get(world, "p1", depth=1, days=30, release_id=release)
    assert _actual(body) == _expect(P1, 30, 1, release="R1")
    orders = _by_id(body)["s:orders"]
    # Only skipped executions in R1: measured as nothing, not as 0%.
    assert orders["stats"]["executions"] == 1 and orders["measure"] is None


async def test_a_suite_filter_narrows_level_one_to_that_suite(world):
    _, body = await _get(world, "p1", depth=1, days=30, suite_name="PAYMENTS")
    assert [n["id"] for n in body["nodes"]] == ["all", "s:payments"]
    assert body["nodes"][0]["value"] == 4


# ── 3. the rules the plan names ────────────────────────────────────────────


async def test_a_run_deletion_makes_a_test_unknown_not_never(world):
    """M-206b: the deleted run cleared BOTH links and took the rows; the case
    was executed (``source='execution'``), so it is unknown, never "never"."""
    _, body = await _get(world, "p1", depth=3, suite="legacy", class_key="legacy.A", days=30)
    nodes = _by_id(body)
    lost = nodes["t:fp-08"]["stats"]
    assert lost["recency"] == "unknown"
    assert lost["last_executed_at"] is None and lost["staleness_days"] is None
    review = nodes["t:fp-11"]["stats"]  # needs_review is included
    assert (review["recency"], review["staleness_days"]) == ("seen", 40)
    _, planned = await _get(world, "p1", depth=3, suite="legacy", class_key="__none__", days=30)
    assert _by_id(planned)["t:fp-09"]["stats"]["recency"] == "never"
    async with world.sessions() as db:
        links = (await db.execute(text(
            "SELECT first_seen_run_id, last_seen_run_id FROM canonical_test_cases "
            "WHERE id = :id"
        ), {"id": world.p1["canonicals"]["fp-08"]})).one()
    assert tuple(links) == (None, None), "the fixture must exercise the real SET NULL"


async def test_the_class_level_is_files_classes_and_ungrouped(world):
    _, body = await _get(world, "p1", depth=2, suite="payments", days=30)
    labels = {n["id"]: n["label"] for n in body["nodes"][1:]}
    assert labels == {
        f"c:payments{SEP}tests/api/test_pay.py": "tests/api/test_pay.py",
        f"c:payments{SEP}__none__": "(ungrouped)",
        f"c:payments{SEP}com.acme.LiveTest": "com.acme.LiveTest",
    }


async def test_the_map_follows_the_rows_after_a_manual_move(world):
    """fp-12's canonical suite is Payments but it RAN under Orders: it is
    drawn under Orders only, and fp-10 (deleted) is drawn nowhere."""
    seen = {f"{suite}/{node['id']}" for suite, node in await _leaves(world, "p1", 30)}
    assert "orders/t:fp-12" in seen and "payments/t:fp-12" not in seen
    assert not any(item.endswith("t:fp-10") for item in seen)
    # The live-stream row sits under the run's label, not the class it stamped.
    assert "payments/t:fp-06" in seen


async def test_staleness_is_the_runs_date_against_the_request_clock(world):
    """M-206c: the idle test last seen 40 days ago is 40 days stale."""
    _, body = await _get(world, "p1", depth=3, suite="orders",
                         class_key="com.acme.OldTest", days=30)
    old = _by_id(body)["t:fp-07"]["stats"]
    assert (old["executions"], old["staleness_days"]) == (0, 40)
    _, wide = await _get(world, "p1", depth=3, suite="orders",
                         class_key="com.acme.OldTest", days=90)
    assert _by_id(wide)["t:fp-07"]["stats"]["executions"] == 1


async def test_a_deactivated_project_is_never_read(world):
    """M-206e: the SQL's own ``is_active`` guard, under the resolver."""
    from app.services import coverage_map_service as svc
    from app.services.analytics_scope import AnalyticsScope

    scope = AnalyticsScope(
        project_id=world.ids["pd"], allowed_project_ids=None, release_ids=(),
        suite_names=(), days=30,
    )
    async with world.sessions() as db:
        payload = await svc.build_coverage_map(db, scope, svc.CoverageLevel(1), now=FROZEN)
    assert payload["nodes"] == []
    resp = await world.client.get(
        PATH, params={"project_id": str(world.ids["pd"])}, headers=world.admin
    )
    assert resp.status_code != 200 or resp.json()["nodes"] == []


async def test_a_parent_with_no_tests_is_an_empty_tree(world):
    _, body = await _get(world, "p1", depth=2, suite="no-such-suite", days=30)
    assert body["nodes"] == []


# ── 4. the layer: authz, 422s, cap, cache ──────────────────────────────────


async def test_authorisation(world):
    await _get(world, "p1", headers=world.member)
    await _get(world, "p1", headers=world.outsider, expect=403)
    # Canonical tests are per project: no project is refused before any query.
    _, body = await _get(world, "", expect=422)
    assert (body["code"], body["param"]) == ("missing_parameter", "project_id")


@pytest.mark.parametrize(
    "params, code, param",
    [
        ({"depth": 4}, "depth_enum", "depth"),
        ({"depth": 2}, "parent_required", "suite"),
        ({"depth": 3, "suite": "payments"}, "parent_required", "class_key"),
        ({"depth": 1, "suite": "payments"}, "parent_unexpected", "suite"),
        ({"depth": 2, "suite": "s" * 501}, "parent_length", "suite"),
        ({"depth": 2, "suite": "pay\x00"}, "parent_format", "suite"),
        ({"depth": [2, 3], "suite": "payments"}, "repeated_parameter", "depth"),
        ({"days": 366}, "window_days_range", "days"),
    ],
)
async def test_refusals_carry_the_rule_id(world, params, code, param):
    _, body = await _get(world, "p1", expect=422, **params)
    assert (body["code"], body.get("param")) == (code, param)
    assert "pay\x00" not in body["message"] and "s" * 501 not in body["message"]


async def test_hostile_keys_are_data_and_never_an_error(world):
    for value in ("x'; DROP TABLE test_runs; --", "__proto__", ":cm_suite", "a" * 500):
        _, body = await _get(world, "p1", depth=3, suite=value, class_key=value)
        assert body["nodes"] == []
    await _get(world, "p1", depth=1)  # the database is untouched


async def test_more_than_499_children_fold_into_one_other(world):
    _, body = await _get(world, "p4", depth=1, days=30)
    nodes = body["nodes"]
    assert len(nodes) == 500
    assert nodes[0]["value"] == 501
    kept = nodes[1:-1]
    assert [n["id"] for n in kept] == [f"s:s{i:03d}" for i in range(498)]
    other = nodes[-1]
    assert (other["id"], other["label"], other["value"]) == ("other:all", "Other (3)", 3)
    assert other["measure"] is None and other["stats"]["recency"] == "never"
    assert (body["meta"]["truncated"], body["meta"]["truncated_total"]) == (True, 501)


async def test_hostile_names_round_trip_through_every_level(world):
    _, level1 = await _get(world, "p3", depth=1, days=30)
    hostile = [n for n in level1["nodes"] if n["label"] == HOSTILE_SUITE]
    assert len(hostile) == 1
    suite = _key(hostile[0]["id"], "s:")
    assert suite == HOSTILE_SUITE.lower()
    _, level2 = await _get(world, "p3", depth=2, suite=suite)
    (cls_node,) = level2["nodes"][1:]
    assert cls_node["label"] == "__proto__"
    cls = _key(cls_node["id"], f"c:{suite}{SEP}")
    _, level3 = await _get(world, "p3", depth=3, suite=suite, class_key=cls)
    assert [n["label"] for n in level3["nodes"]] == ["__proto__", "constructor"]


async def test_reconciles_with_analytics_coverage_where_it_must(world):
    """One suite, every test executed: the map's root is /analytics/coverage's
    summary. Across suites a test that ran under two suites counts twice in the
    map's sum (declared in meta.definitions.suite_totals) and once there."""
    from app.services import analytics_service

    _, body = await _get(world, "p1", depth=1, days=30, suite_name="Payments")
    async with world.sessions() as db:
        coverage = await analytics_service.coverage_stats(
            db, str(world.ids["p1"]), 30, suite_name="Payments",
        )
    summary = coverage["summary"]
    root = body["nodes"][0]
    assert root["value"] == summary["unique_tests"] == 4
    assert root["stats"]["executions"] == summary["total_executions"] == 7
    assert round(root["measure"], 1) == float(summary["avg_pass_rate"])

    _, p3 = await _get(world, "p3", depth=1, days=30)
    async with world.sessions() as db:
        p3_coverage = await analytics_service.coverage_stats(db, str(world.ids["p3"]), 30)
    leaves = await _leaves(world, "p3", 30)
    executed = sum(1 for _, node in leaves if node["stats"]["executions"] > 0)
    # fp-dual ran under Alpha AND Beta: once there, twice here.
    assert p3_coverage["summary"]["unique_tests"] == 2
    assert executed == 3
    assert "counted under both" in p3["meta"]["definitions"]["suite_totals"]


async def test_a_repeat_is_cached_and_a_canonical_move_invalidates_it(world):
    """The VIZ-212 bump on the move path, through the real route: without it
    the third call is a stale cache hit."""
    first, before = await _get(world, "p3", depth=1, days=7)
    second, again = await _get(world, "p3", depth=1, days=7)
    assert first.headers["X-Analytics-Cache"] == "miss"
    assert second.headers["X-Analytics-Cache"] == "hit"
    assert again["nodes"] == before["nodes"]

    canonical_id = world.p3["canonicals"]["fp-idle"]
    resp = await world.client.post(
        f"/api/v1/canonical-test-cases/{canonical_id}/link",
        json={"test_suite_id": str(world.p3["suites"]["Beta"])}, headers=world.admin,
    )
    assert resp.status_code == 200, resp.text

    third, after = await _get(world, "p3", depth=1, days=7)
    assert third.headers["X-Analytics-Cache"] == "miss", "the move did not invalidate the map"
    was, now = _by_id(before), _by_id(after)
    assert now["s:alpha"]["value"] == was["s:alpha"]["value"] - 1
    assert now["s:beta"]["value"] == was["s:beta"]["value"] + 1


async def test_a_retired_case_leaves_the_map(world):
    """The real reconciler on real rows; its epoch bump belongs to the
    committing caller (``tests/regression/test_analytics_epoch_mutation_paths.py``)."""
    from app.services import test_suite_service

    _, before = await _get(world, "p3", depth=1, days=6)
    async with world.sessions() as db:
        counts = await test_suite_service.reconcile_canonical_deletions(
            db, world.ids["p3"], window_runs=5,
        )
        await db.commit()
    assert counts["deleted"] == 1  # fp-retire; fp-idle is needs_review, untouched
    _, after = await _get(world, "p3", depth=1, days=5)
    assert _by_id(after)["s:beta"]["value"] == _by_id(before)["s:beta"]["value"] - 1


async def test_a_suite_rename_invalidates_the_map(world):
    """The rename route bumps after its commit: the cached map is replaced and
    the idle test moved to Beta above now sits under the new name."""
    await _get(world, "p3", depth=1, days=4)
    cached, _ = await _get(world, "p3", depth=1, days=4)
    assert cached.headers["X-Analytics-Cache"] == "hit"
    resp = await world.client.patch(
        f"/api/v1/suites/{world.p3['suites']['Beta']}", json={"name": "Gamma"},
        headers=world.admin,
    )
    assert resp.status_code == 200, resp.text
    fresh, after = await _get(world, "p3", depth=1, days=4)
    assert fresh.headers["X-Analytics-Cache"] == "miss"
    assert "s:gamma" in _by_id(after)
