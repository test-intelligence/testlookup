"""E3 against real Postgres: ``/runs`` and ``/me/assigned-failures`` (list and
count) with REPEATABLE ``release_id`` / ``suite_name``.

Hand-built rows, so every expected set below is written out, not derived:

    project P1 (member M)            releases R1, R2        project P2: release R9
    run  release  trigger       label      rows (suite, status, assigned to M?)
    A    R1       api           Payments   Payments FAILED yes, Cart FAILED yes
    B    R2       api           Cart       Cart BROKEN yes
    C    --       api           Search     Search FAILED yes
    D    R1       api           Search     Search PASSED no
    E    R2       live_stream   Payments   SomeClass FAILED yes   (effective suite: Payments)
    F    R9 (P2)  api           Payments   Payments FAILED no

Proves: OR within a dimension, AND across; the ``unattributed`` sentinel mixes
with real ids; one forbidden (or unknown) id among readable ones is the
403/404 and no rows; the C1 cap; and that a ONE-value request runs exactly the
statements the pre-E3 route ran (it handed the service the same scalars).

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head. Uses ``REDIS_URL``
when set, else ``fakeredis``. Every row carries a unique tag and is removed.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not configured")
    return value


def _redis_client():
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        import redis.asyncio as redis_asyncio

        return redis_asyncio.Redis.from_url(url, decode_responses=True)
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


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

    tag = uuid.uuid4().hex[:10]
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    member, outsider = uuid.uuid4(), uuid.uuid4()
    r1, r2, r9 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    runs = {key: uuid.uuid4() for key in "ABCDEF"}
    when = datetime.now(timezone.utc) - timedelta(days=1)
    try:
        async with sessions() as db:
            for pid, label in ((p1, "p1"), (p2, "p2")):
                db.add(Project(
                    id=pid, name=f"e3multi-{label}-{tag}", slug=f"e3multi-{label}-{tag}",
                    is_active=True, description=f"throwaway E3 multi-scope {tag}",
                ))
            for uid, label in ((member, "member"), (outsider, "outsider")):
                db.add(User(
                    id=uid, email=f"e3multi-{label}-{tag}@example.com",
                    username=f"e3multi_{label}_{tag}", full_name=f"E3 {label}",
                    hashed_password="!unusable", role=UserRole.QA_ENGINEER.value,
                ))
            await db.flush()
            db.add(ProjectMember(project_id=p1, user_id=member, role=UserRole.QA_ENGINEER.value))
            db.add(ProjectMember(project_id=p2, user_id=outsider, role=UserRole.QA_ENGINEER.value))
            for rid, pid, name in ((r1, p1, "r1"), (r2, p1, "r2"), (r9, p2, "r9")):
                db.add(Release(id=rid, project_id=pid, name=f"e3multi-{name}-{tag}",
                               version=name, status="active"))
            await db.flush()
            spec = {
                # key: (project, release, trigger, label, [(suite, status, assigned)])
                "A": (p1, r1, "api", "Payments", [("Payments", "FAILED", True), ("Cart", "FAILED", True)]),
                "B": (p1, r2, "api", "Cart", [("Cart", "BROKEN", True)]),
                "C": (p1, None, "api", "Search", [("Search", "FAILED", True)]),
                "D": (p1, r1, "api", "Search", [("Search", "PASSED", False)]),
                "E": (p1, r2, "live_stream", "Payments", [("SomeClass", "FAILED", True)]),
                "F": (p2, r9, "api", "Payments", [("Payments", "FAILED", False)]),
            }
            for index, (key, (pid, rid, trigger, label, rows)) in enumerate(spec.items()):
                db.add(TestRun(
                    id=runs[key], project_id=pid, build_number=f"e3multi-{key}-{tag}",
                    jenkins_job="e3multi", status=LaunchStatus.FAILED, ingestion_source="unknown",
                    trigger_source=trigger, primary_suite_name=label, primary_release_id=rid,
                    total_tests=len(rows), created_at=when + timedelta(minutes=index),
                ))
            await db.flush()
            for index, (key, (_pid, _rid, _trigger, _label, rows)) in enumerate(spec.items()):
                for n, (suite, status, assigned) in enumerate(rows):
                    db.add(TestCase(
                        test_run_id=runs[key], test_name=f"t_{key}_{n}", suite_name=suite,
                        test_fingerprint=f"e3multi-{tag}-{key}-{n}",
                        class_name=f"C{key}", status=status,
                        assigned_to_user_id=member if assigned else None,
                        created_at=when + timedelta(minutes=index, seconds=n),
                    ))
            await db.commit()

        def _jwt(uid):
            return {"Authorization": f"Bearer {create_access_token(str(uid))}"}

        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        try:
            yield SimpleNamespace(
                client=client, engine=engine, p1=p1, p2=p2, r1=str(r1), r2=str(r2), r9=str(r9),
                runs={key: str(value) for key, value in runs.items()},
                member=_jwt(member), outsider=_jwt(outsider), tag=tag,
            )
        finally:
            await client.aclose()
    finally:
        app.dependency_overrides.pop(get_db, None)
        for statement in (
            text("DELETE FROM projects WHERE id IN (:a, :b)"),
            text("DELETE FROM access_audit_logs WHERE actor_user_id IN (:u1, :u2)"),
            delete(User).where(User.email.like(f"e3multi-%-{tag}@example.com")),
        ):
            try:
                async with sessions.begin() as db:
                    await db.execute(statement, {"a": p1, "b": p2, "u1": member, "u2": outsider})
            except Exception as exc:  # noqa: BLE001 -- report, keep cleaning
                print(f"e3multi teardown: {type(exc).__name__}: {str(exc)[:160]}")
        patch.undo()
        await redis.aclose()
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()


# ── helpers ─────────────────────────────────────────────────────────────────


async def _get(world, path, params, headers=None):
    resp = await world.client.get(path, params=params, headers=headers or world.member)
    return resp.status_code, resp.json()


async def _run_keys(world, *filters) -> set[str]:
    status, body = await _get(
        world, "/api/v1/runs", [("project_id", str(world.p1)), ("size", "100"), *filters]
    )
    assert status == 200, body
    by_id = {rid: key for key, rid in world.runs.items()}
    keys = {by_id[item["id"]] for item in body["items"]}
    assert body["total"] == len(keys), body
    return keys


async def _inbox(world, *filters) -> set[str]:
    params = [("project_id", str(world.p1)), ("size", "100"), *filters]
    status, body = await _get(world, "/api/v1/me/assigned-failures", params)
    assert status == 200, body
    names = {item["test_name"] for item in body["items"]}
    assert body["total"] == len(names), body
    # The badge answers the list's question: same filters, same number.
    status, counted = await _get(world, "/api/v1/me/assigned-failures/count", params[:1] + list(filters))
    assert status == 200, counted
    assert counted["count"] == len(names), (filters, counted, names)
    return names


# ── /runs ───────────────────────────────────────────────────────────────────


async def test_runs_no_filter_is_every_run_of_the_project(world):
    assert await _run_keys(world) == set("ABCDE")


async def test_runs_releases_are_or_within_the_dimension(world):
    r1, r2 = ("release_id", world.r1), ("release_id", world.r2)
    unattributed = ("release_id", "unattributed")
    assert await _run_keys(world, r1) == {"A", "D"}
    assert await _run_keys(world, r2) == {"B", "E"}
    assert await _run_keys(world, r1, r2) == {"A", "B", "D", "E"}
    assert await _run_keys(world, unattributed) == {"C"}
    assert await _run_keys(world, r1, unattributed) == {"A", "C", "D"}


async def test_runs_suites_are_or_and_releases_and_suites_are_anded(world):
    pay, cart, search = ("suite_name", "Payments"), ("suite_name", "cart"), ("suite_name", " SEARCH ")
    assert await _run_keys(world, pay) == {"A", "E"}
    # A holds a Cart row, B is labelled Cart: the list's run-level rule.
    assert await _run_keys(world, pay, cart) == {"A", "B", "E"}
    assert await _run_keys(world, search) == {"C", "D"}
    assert await _run_keys(world, ("release_id", world.r1), pay, search) == {"A", "D"}
    assert await _run_keys(world, ("release_id", world.r1), ("release_id", world.r2), cart) == {"A", "B"}
    assert await _run_keys(world, ("release_id", world.r2), search) == set()


async def test_runs_one_value_runs_the_legacy_statements(world):
    """The pre-E3 route handed ``list_project_runs`` one scalar release and one
    scalar suite; the request must still execute exactly those statements."""
    from app.services import runs_service

    captured: list[str] = []

    def _capture(_conn, _cursor, statement, *_a):
        captured.append(statement)

    event.listen(world.engine.sync_engine, "before_cursor_execute", _capture)
    try:
        await _get(world, "/api/v1/runs", [
            ("project_id", str(world.p1)), ("release_id", world.r1), ("suite_name", "Payments"),
        ])
        via_route = [s for s in captured if "FROM test_runs" in s]
        captured.clear()
        async with async_sessionmaker(world.engine, expire_on_commit=False)() as db:
            items, total, _ = await runs_service.list_project_runs(
                db, str(world.p1), 1, 20, None, world.r1,
                accessible_project_ids={world.p1}, days=30, suite_name="Payments",
            )
        legacy = [s for s in captured if "FROM test_runs" in s]
    finally:
        event.remove(world.engine.sync_engine, "before_cursor_execute", _capture)
    assert via_route and via_route == legacy
    assert "primary_release_id = " in legacy[0] and "primary_release_id IN" not in legacy[0]
    assert total == 1


async def test_runs_a_forbidden_or_unknown_id_among_readable_ones_returns_nothing(world):
    params = [("project_id", str(world.p1))]
    status, body = await _get(world, "/api/v1/runs", params + [
        ("release_id", world.r1), ("release_id", world.r9),
    ])
    assert status == 403, body
    assert "items" not in body
    # The legacy single-id answer, for comparison: the same 403.
    status, _ = await _get(world, "/api/v1/runs", params + [("release_id", world.r9)])
    assert status == 403
    status, body = await _get(world, "/api/v1/runs", params + [
        ("release_id", world.r1), ("release_id", str(uuid.uuid4())),
    ])
    assert status == 404, body


async def test_runs_the_release_cap_is_the_c1_body(world):
    ids = [("release_id", str(uuid.uuid4())) for _ in range(21)]
    status, body = await _get(world, "/api/v1/runs", [("project_id", str(world.p1)), *ids])
    assert status == 422
    assert body["code"] == "release_cap" and body["allowed"] == {"max": 20}


# ── /me/assigned-failures (list + count) ────────────────────────────────────

A_PAY, A_CART, B_CART, C_SEARCH, E_LIVE = "t_A_0", "t_A_1", "t_B_0", "t_C_0", "t_E_0"


async def test_inbox_no_filter(world):
    assert await _inbox(world) == {A_PAY, A_CART, B_CART, C_SEARCH, E_LIVE}


async def test_inbox_releases_are_or_within_the_dimension(world):
    r1, r2 = ("release_id", world.r1), ("release_id", world.r2)
    assert await _inbox(world, r1) == {A_PAY, A_CART}
    assert await _inbox(world, r1, r2) == {A_PAY, A_CART, B_CART, E_LIVE}
    assert await _inbox(world, ("release_id", "unattributed")) == {C_SEARCH}
    assert await _inbox(world, r1, ("release_id", "unattributed")) == {A_PAY, A_CART, C_SEARCH}


async def test_inbox_suites_use_the_effective_suite_and_and_with_releases(world):
    cart, pay, search = ("suite_name", "Cart"), ("suite_name", "payments"), ("suite_name", "Search")
    assert await _inbox(world, cart) == {A_CART, B_CART}
    # E's row says SomeClass; a live-stream run's label is its effective suite.
    assert await _inbox(world, pay) == {A_PAY, E_LIVE}
    assert await _inbox(world, cart, search) == {A_CART, B_CART, C_SEARCH}
    assert await _inbox(world, ("release_id", world.r2), pay) == {E_LIVE}
    assert await _inbox(
        world, ("release_id", world.r1), ("release_id", world.r2), cart, pay,
    ) == {A_PAY, A_CART, B_CART, E_LIVE}


async def test_inbox_a_forbidden_id_among_readable_ones_is_the_403(world):
    for path in ("/api/v1/me/assigned-failures", "/api/v1/me/assigned-failures/count"):
        status, body = await _get(world, path, [
            ("project_id", str(world.p1)), ("release_id", world.r1), ("release_id", world.r9),
        ])
        assert status == 403, (path, body)
        assert "items" not in body and "count" not in body
