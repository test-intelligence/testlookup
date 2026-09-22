"""E3 / VIZ-302 against real Postgres: ``/metrics/summary`` ``report_metrics``.

Hand-built rows under a frozen clock, every expected number computed by hand
below -- never derived from the code under test.

Clock: 2026-09-21 12:00 UTC, ``days=7``:
current  = [09-14 12:00, 09-21 12:00)     previous = [09-07 12:00, 09-14 12:00)

    run  when   release  label     aggregates (total/pass/fail/broken/skip)  duration  rows (suite: statuses)
    C1   09-20  R1       Payments  10 / 7 / 1 / 1 / 1                          1000      Payments: 5P 1F; Cart: 2P 1B 1S
    C2   09-19  --       Cart       5 / 3 / 0 / 0 / 0   (2 without a verdict)  NULL      (none -- mid-ingest)
    C3   09-15  R1       Payments   4 / 4 / 0 / 0 / 0                          3000      Payments: 4P
    P1   09-10  R1       Payments   8 / 6 / 2 / 0 / 0                          2000      Payments: 6P 2F
    P2   09-08  --       Cart       3 / 1 / 1 / 0 / 1                           500      Cart: 1P 1F 1S
    O1   08-01  R1       Payments   2 / 2 / 0 / 0 / 0                           100      Payments: 2P
    O2   08-02  R1       Cart       1 / 1 / 0 / 0 / 0                           100      Cart: 1P

Proves the new fields over real rows, that they agree with the existing fields
of the same response (same scope, same basis), ``comparable`` true and false
for every reason code, and unmeasured -> ``null`` with a reason, never 0.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head. Uses ``REDIS_URL``
when set, else ``fakeredis``. Every row carries a unique tag and is removed.
"""
from __future__ import annotations

import importlib
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

FROZEN = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
_TIME_MODULES = (
    "app.services.metrics_service",
    "app.services.report_metrics_service",
    "app.services.analytics_meta",
    "app.services.analytics_scope",
)


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


def _at(month: int, day: int) -> datetime:
    return datetime(2026, month, day, 18, 0, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
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

    patch = pytest.MonkeyPatch()
    app.dependency_overrides[get_db] = _get_db
    patch.setattr("app.db.redis_client.get_redis", lambda: redis)
    patch.setattr("app.db.postgres.AsyncSessionLocal", sessions, raising=False)
    for name in _TIME_MODULES:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(module, "datetime"):
            patch.setattr(module, "datetime", _FrozenDatetime)

    tag = uuid.uuid4().hex[:10]
    project, member, r1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # key: (when, release, label, (total, passed, failed, broken, skipped), duration, rows)
    spec = {
        "C1": (_at(9, 20), r1, "Payments", (10, 7, 1, 1, 1), 1000,
               [("Payments", "PASSED")] * 5 + [("Payments", "FAILED"), ("Cart", "PASSED"),
                                               ("Cart", "PASSED"), ("Cart", "BROKEN"), ("Cart", "SKIPPED")]),
        "C2": (_at(9, 19), None, "Cart", (5, 3, 0, 0, 0), None, []),
        "C3": (_at(9, 15), r1, "Payments", (4, 4, 0, 0, 0), 3000, [("Payments", "PASSED")] * 4),
        "P1": (_at(9, 10), r1, "Payments", (8, 6, 2, 0, 0), 2000,
               [("Payments", "PASSED")] * 6 + [("Payments", "FAILED")] * 2),
        "P2": (_at(9, 8), None, "Cart", (3, 1, 1, 0, 1), 500,
               [("Cart", "PASSED"), ("Cart", "FAILED"), ("Cart", "SKIPPED")]),
        "O1": (_at(8, 1), r1, "Payments", (2, 2, 0, 0, 0), 100, [("Payments", "PASSED")] * 2),
        "O2": (_at(8, 2), r1, "Cart", (1, 1, 0, 0, 0), 100, [("Cart", "PASSED")]),
    }
    try:
        async with sessions() as db:
            db.add(Project(id=project, name=f"e3metrics-{tag}", slug=f"e3metrics-{tag}",
                           is_active=True, description=f"throwaway E3 report metrics {tag}"))
            db.add(User(id=member, email=f"e3metrics-member-{tag}@example.com",
                        username=f"e3metrics_member_{tag}", full_name="E3 metrics member",
                        hashed_password="!unusable", role=UserRole.QA_ENGINEER.value))
            await db.flush()
            db.add(ProjectMember(project_id=project, user_id=member, role=UserRole.QA_ENGINEER.value))
            db.add(Release(id=r1, project_id=project, name=f"e3metrics-r1-{tag}", version="1",
                           status="active"))
            await db.flush()
            for key, (when, rid, label, (total, p, f, b, s), duration, rows) in spec.items():
                run_id = uuid.uuid4()
                db.add(TestRun(
                    id=run_id, project_id=project, build_number=f"e3metrics-{key}-{tag}",
                    jenkins_job="e3metrics", status=LaunchStatus.PASSED, ingestion_source="unknown",
                    trigger_source="api", primary_suite_name=label, primary_release_id=rid,
                    total_tests=total, passed_tests=p, failed_tests=f, broken_tests=b,
                    skipped_tests=s, duration_ms=duration, created_at=when,
                ))
                await db.flush()
                for n, (suite, status) in enumerate(rows):
                    db.add(TestCase(
                        test_run_id=run_id, test_name=f"t_{key}_{n}", suite_name=suite,
                        test_fingerprint=f"e3metrics-{tag}-{key}-{n}", status=status,
                        created_at=when,
                    ))
            await db.commit()

        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        headers = {"Authorization": f"Bearer {create_access_token(str(member))}"}
        try:
            yield SimpleNamespace(client=client, project=str(project), r1=str(r1), headers=headers)
        finally:
            await client.aclose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            text("DELETE FROM projects WHERE id = :p"),
            text("DELETE FROM access_audit_logs WHERE actor_user_id = :u"),
            delete(User).where(User.email.like(f"e3metrics-%-{tag}@example.com")),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement, {"p": project, "u": member})
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"e3metrics teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


async def _summary(world, *filters, days: int = 7) -> dict:
    # The block is opt-in (fix round C, m1): the strip asks for it by name.
    resp = await world.client.get(
        "/api/v1/metrics/summary",
        params=[("project_id", world.project), ("days", str(days)), *filters,
                ("include", "report_metrics")],
        headers=world.headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    from app.models.viz_contracts import validate_contract

    # Every payload the endpoint produces is a valid C6 object.
    validate_contract("report_metrics", body["report_metrics"])
    return body


def _values(period: dict) -> dict:
    return {k: period[k] for k in (
        "runs", "total_tests", "passed", "failed", "broken", "skipped", "unknown",
        "pass_rate", "total_duration_ms", "avg_duration_ms", "duration_runs",
    )}


def _agrees_with_the_existing_fields(body: dict) -> None:
    """Same scope and basis as the fields that were already there."""
    cur = body["report_metrics"]["current"]
    assert cur["runs"] == body["meta"]["totals"]["matched_runs"]
    assert cur["total_tests"] == body["total_executions_7d"]["value"]
    assert cur["pass_rate"] == body["avg_pass_rate_7d"]["value"]
    assert cur["avg_duration_ms"] == body["avg_duration_ms"]["value"]


# ── the numbers ─────────────────────────────────────────────────────────────


async def test_unfiltered_current_and_previous(world):
    body = await _summary(world)
    metrics = body["report_metrics"]
    assert metrics["schema_version"] == 1 and metrics["pass_rate_basis"] == "executions"
    # C1 + C2 + C3. pass rate 14 / (14 + 1 + 1). Durations: C1 + C3 (C2 has none).
    assert _values(metrics["current"]) == {
        "runs": 3, "total_tests": 19, "passed": 14, "failed": 1, "broken": 1, "skipped": 1,
        "unknown": 2, "pass_rate": 87.5, "total_duration_ms": 4000, "avg_duration_ms": 2000,
        "duration_runs": 2,
    }
    # P1 + P2. pass rate 7 / (7 + 3).
    assert _values(metrics["previous"]) == {
        "runs": 2, "total_tests": 11, "passed": 7, "failed": 3, "broken": 0, "skipped": 1,
        "unknown": 0, "pass_rate": 70.0, "total_duration_ms": 2500, "avg_duration_ms": 1250,
        "duration_runs": 2,
    }
    assert metrics["current"]["window"] == {"from": "2026-09-14", "to": "2026-09-21", "days": 7}
    assert metrics["previous"]["window"] == {"from": "2026-09-07", "to": "2026-09-14", "days": 7}
    assert metrics["previous"]["comparable"] is True
    assert metrics["previous"]["reason"] is None and metrics["previous"]["reason_code"] is None
    _agrees_with_the_existing_fields(body)


async def test_one_release(world):
    body = await _summary(world, ("release_id", world.r1))
    metrics = body["report_metrics"]
    # C1 + C3; pass rate 11 / 13 = 84.615...
    assert _values(metrics["current"]) == {
        "runs": 2, "total_tests": 14, "passed": 11, "failed": 1, "broken": 1, "skipped": 1,
        "unknown": 0, "pass_rate": 84.6, "total_duration_ms": 4000, "avg_duration_ms": 2000,
        "duration_runs": 2,
    }
    assert _values(metrics["previous"])["runs"] == 1
    assert metrics["previous"]["pass_rate"] == 75.0
    assert metrics["previous"]["comparable"] is True
    _agrees_with_the_existing_fields(body)


async def test_suite_counts_rows_by_suite_and_marks_a_different_basis(world):
    # Payments: C1's 6 Payments rows + C3's 4; P1's 8. No row-less run either side.
    body = await _summary(world, ("suite_name", "payments"))
    metrics = body["report_metrics"]
    assert _values(metrics["current"]) == {
        "runs": 2, "total_tests": 10, "passed": 9, "failed": 1, "broken": 0, "skipped": 0,
        "unknown": 0, "pass_rate": 90.0, "total_duration_ms": 4000, "avg_duration_ms": 2000,
        "duration_runs": 2,
    }
    assert _values(metrics["previous"])["total_tests"] == 8
    assert metrics["previous"]["comparable"] is True
    _agrees_with_the_existing_fields(body)

    # Cart: C1's 4 Cart rows + C2's run totals (no rows yet: 3 passed, 2 no verdict).
    body = await _summary(world, ("suite_name", "Cart"))
    metrics = body["report_metrics"]
    assert _values(metrics["current"]) == {
        "runs": 2, "total_tests": 9, "passed": 5, "failed": 0, "broken": 1, "skipped": 1,
        "unknown": 2, "pass_rate": 83.3, "total_duration_ms": 1000, "avg_duration_ms": 1000,
        "duration_runs": 1,
    }
    assert _values(metrics["previous"]) == {
        "runs": 1, "total_tests": 3, "passed": 1, "failed": 1, "broken": 0, "skipped": 1,
        "unknown": 0, "pass_rate": 50.0, "total_duration_ms": 500, "avg_duration_ms": 500,
        "duration_runs": 1,
    }
    # The current window counts C2 by its run totals, the previous counts rows only.
    assert metrics["previous"]["comparable"] is False
    assert metrics["previous"]["reason_code"] == "different_basis"
    assert metrics["previous"]["reason"]
    _agrees_with_the_existing_fields(body)


# ── comparable: false, for each reason ─────────────────────────────────────


async def test_history_starting_inside_the_previous_window_is_partial(world):
    body = await _summary(world, ("release_id", "unattributed"))
    metrics = body["report_metrics"]
    # C2 alone: nothing evaluated has failed, 3 of 3 evaluated passed; no duration.
    assert _values(metrics["current"]) == {
        "runs": 1, "total_tests": 5, "passed": 3, "failed": 0, "broken": 0, "skipped": 0,
        "unknown": 2, "pass_rate": 100.0, "total_duration_ms": None, "avg_duration_ms": None,
        "duration_runs": 0,
    }
    reasons = metrics["current"]["reasons"]
    assert reasons["total_duration_ms"] and reasons["avg_duration_ms"]
    # The first unattributed run (P2, 09-08) is after the previous window opens (09-07).
    previous = metrics["previous"]
    assert previous["comparable"] is False and previous["reason_code"] == "partial_window"
    assert "2026-09-08" in previous["reason"]
    # The existing field keeps its historical 0 for an unmeasured duration.
    assert body["avg_duration_ms"]["value"] == 0


async def test_no_runs_in_the_previous_window(world):
    # days=2: current [09-19 12:00, 09-21 12:00) holds C1 for R1; previous holds nothing.
    body = await _summary(world, ("release_id", world.r1), days=2)
    previous = body["report_metrics"]["previous"]
    assert body["report_metrics"]["current"]["runs"] == 1
    assert previous["comparable"] is False and previous["reason_code"] == "no_data"
    assert previous["runs"] is None and previous["reasons"]["runs"]


async def test_nothing_in_scope_is_null_with_reasons_never_zero(world):
    body = await _summary(world, ("suite_name", "no-such-suite"))
    current = body["report_metrics"]["current"]
    assert set(_values(current).values()) == {None}
    assert all(current["reasons"][key] for key in _values(current))
    previous = body["report_metrics"]["previous"]
    assert previous["comparable"] is False and previous["reason_code"] == "not_measured"
    # The existing fields are untouched: they still say 0.
    assert body["avg_pass_rate_7d"]["value"] == 0.0
    assert body["total_executions_7d"]["value"] == 0


# ── opt-in (fix round C, m1) ────────────────────────────────────────────────


async def test_without_include_the_block_is_not_computed(world, monkeypatch):
    """No ``include=report_metrics``: the pre-VIZ-302 request. The block is not
    in the body and its builder never runs -- no block statements -- and the
    fields that were there hold the values the opt-in request reports. (The
    cache key is pinned by ``tests/test_report_metrics.py``.)"""
    from app.services import report_metrics_service

    calls: list = []
    real = report_metrics_service.build_report_metrics

    async def _spy(*args, **kwargs):
        calls.append(args)
        return await real(*args, **kwargs)

    monkeypatch.setattr(report_metrics_service, "build_report_metrics", _spy)
    # days=6: a scope no other test requests, so neither call is a cache hit.
    params = [("project_id", world.project), ("days", "6"), ("suite_name", "payments")]
    resp = await world.client.get("/api/v1/metrics/summary", params=params, headers=world.headers)
    assert resp.status_code == 200, resp.text
    plain = resp.json()
    assert "report_metrics" not in plain
    assert calls == []

    resp = await world.client.get(
        "/api/v1/metrics/summary", params=[*params, ("include", "report_metrics")],
        headers=world.headers,
    )
    assert resp.status_code == 200, resp.text
    opted = resp.json()
    assert "report_metrics" in opted and len(calls) == 1
    for key in plain:
        if key != "meta":
            assert opted[key] == plain[key], key
