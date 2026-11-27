"""The run list, run detail and run tests reads issue a fixed number of SQL statements (GAP-C04, C05, C09).

On real PostgreSQL, through the ASGI app, with the statements counted on the engine
(``StatementCounter`` from ``tests/integration/defect_w06_world.py``):

* ``GET /api/v1/runs`` takes the SAME number of statements for a page of 5 runs and a page of 50
  (the release, run-number and suite lookups are one batched query per page, never one per run),
  and that number is pinned;
* ``GET /api/v1/runs/{id}`` takes a pinned number of statements;
* ``GET /api/v1/runs/{id}/tests`` takes the same number for a page of 5 cases and a page of 50;
* a ``size`` above the declared bound is a 422 that never reaches ``test_runs`` / ``test_cases``.

The world is seeded through the ingestion path (``create_run_from_payload`` +
``ingest_test_results`` + ``_update_run_aggregates``), with releases linked by
``link_run_to_release``, so the rows look like real runs. Counts, not time: nothing here reads a clock.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (skips only without it) and ``REDIS_URL``
(else fakeredis). One raising cleanup deletes everything seeded.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from tests.integration.defect_w06_world import StatementCounter

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

RUNS = 60  # small runs, three cases each, plus one large run below
LARGE_RUN_CASES = 60
SUITES = ("perf-api", "perf-ui", "perf-db")

# A per-row lookup raises the count with the page size and fails the equal-count assertions.
# The exact counts also catch added statements that run once per request.
# list: token revocation x2, user, current membership, COUNT, page, release map, run-number pairs,
# run numbers, suite map.
LIST_STATEMENTS = 10
# detail: token revocation x2, user, run access (run project, membership), run, release map, project name,
# run-number pairs, run numbers, suite map.
DETAIL_STATEMENTS = 11
# tests: token revocation x2, user, run access (run project, membership), COUNT, page.
TESTS_STATEMENTS = 7


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _redis_client():
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        import redis.asyncio as redis_asyncio

        return redis_asyncio.Redis.from_url(url, decode_responses=True)
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _results(index: int, cases: int) -> list[dict]:
    return [
        {
            "test_name": f"test_perf_{index:03d}_{n:03d}",
            "class_name": f"perf.Case{index:03d}",
            "suite_name": SUITES[n % len(SUITES)],
            "status": "failed" if n == 0 else "passed",
            "duration_ms": 10 + n,
            "error_message": "AssertionError: expected 1 got 2" if n == 0 else None,
        }
        for n in range(cases)
    ]


async def _cleanup(
    sessions, project_ids: list[uuid.UUID], user_ids: list[uuid.UUID]
) -> None:
    """Delete everything this module seeded, then prove it is gone. Raises on any leftover."""
    errors: list[str] = []
    for statement, params in (
        (
            "DELETE FROM canonical_test_cases WHERE project_id = ANY(:p)",
            {"p": project_ids},
        ),
        ("DELETE FROM projects WHERE id = ANY(:p)", {"p": project_ids}),
        (
            "DELETE FROM access_audit_logs WHERE actor_user_id = ANY(:u)",
            {"u": user_ids},
        ),
        ("DELETE FROM users WHERE id = ANY(:u)", {"u": user_ids}),
    ):
        try:
            async with sessions.begin() as db:
                await db.execute(text(statement), params)
        except Exception as exc:  # noqa: BLE001 -- keep cleaning, then raise
            errors.append(
                f"{statement.split(' WHERE')[0]}: {type(exc).__name__}: {str(exc)[:160]}"
            )
    async with sessions() as db:
        leftovers = {
            "projects": await db.scalar(
                text("SELECT count(*) FROM projects WHERE id = ANY(:p)"),
                {"p": project_ids},
            ),
            "test_runs": await db.scalar(
                text("SELECT count(*) FROM test_runs WHERE project_id = ANY(:p)"),
                {"p": project_ids},
            ),
            "releases": await db.scalar(
                text("SELECT count(*) FROM releases WHERE project_id = ANY(:p)"),
                {"p": project_ids},
            ),
            "users": await db.scalar(
                text("SELECT count(*) FROM users WHERE id = ANY(:u)"), {"u": user_ids}
            ),
        }
    leaked = {table: n for table, n in leftovers.items() if n}
    if errors or leaked:
        raise AssertionError(
            f"perf-runs cleanup failed: errors={errors} leftovers={leaked}"
        )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    pytest.importorskip("asyncpg")
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import create_access_token
    from app.db import postgres as app_postgres
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import Project, ProjectMember, Release, User, UserRole
    from app.services.ingestion import _update_run_aggregates
    from app.services.ingestion_pipeline import (
        create_run_from_payload,
        ingest_test_results,
    )
    from app.services.release_linker import link_run_to_release

    dsn = _dsn()
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

    tag = "pc" + uuid.uuid4().hex[:8]
    project_id, member_id = uuid.uuid4(), uuid.uuid4()
    releases = (uuid.uuid4(), uuid.uuid4())
    client = None
    try:
        async with sessions.begin() as db:
            db.add(
                Project(id=project_id, name=f"perf-runs-{tag}", slug=f"perf-runs-{tag}")
            )
            db.add(
                User(
                    id=member_id,
                    email=f"perf-runs-{tag}@example.test",
                    username=f"perf_runs_{tag}",
                    full_name="Perf runs member",
                    hashed_password="!unusable",
                    role=UserRole.QA_ENGINEER.value,
                )
            )
            await db.flush()
            db.add(
                ProjectMember(
                    project_id=project_id,
                    user_id=member_id,
                    role=UserRole.QA_ENGINEER.value,
                )
            )
            for n, release_id in enumerate(releases):
                db.add(
                    Release(
                        id=release_id,
                        project_id=project_id,
                        name=f"perf-r{n}-{tag}",
                        version=f"9.{n}.0",
                        status="planning",
                    )
                )

        run_ids: list[uuid.UUID] = []
        for index in range(RUNS + 1):
            cases = LARGE_RUN_CASES if index == RUNS else 3
            async with sessions.begin() as db:
                run = await create_run_from_payload(
                    db,
                    project_id=str(project_id),
                    build_number=f"{tag}-{index:03d}",
                    ingestion_source="sdk",
                    ci_provider="github_actions",
                    ci_repo="example/perf",
                    ci_run_url=f"https://ci.example.test/perf/{tag}/{index}",
                )
                accepted = await ingest_test_results(db, run, _results(index, cases))
                assert accepted == cases
                await db.flush()
                await _update_run_aggregates(db, run.id)
                if index % 3 != 2:
                    await link_run_to_release(
                        db,
                        releases[index % 3],
                        run.id,
                        link_source="manual",
                        project_id=project_id,
                    )
                run_ids.append(run.id)

        client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )
        headers = {"Authorization": f"Bearer {create_access_token(str(member_id))}"}
        # Warm the process (imports, lazily built caches) so every measured request starts equal.
        warm = await client.get(
            "/api/v1/runs",
            params={"project_id": str(project_id), "size": 1},
            headers=headers,
        )
        assert warm.status_code == 200, warm.text[:300]
        yield SimpleNamespace(
            engine=engine,
            sessions=sessions,
            client=client,
            headers=headers,
            project_id=project_id,
            run_ids=run_ids,
            small_run=run_ids[0],
            large_run=run_ids[RUNS],
            releases=releases,
            tag=tag,
        )
    finally:
        if client is not None:
            await client.aclose()
        app.dependency_overrides.pop(get_db, None)
        patch.undo()
        try:
            await _cleanup(sessions, [project_id], [member_id])
        finally:
            await redis.aclose()
            await engine.dispose()
            await app_postgres.dispose_engine_for_loop()


def _shape(statements: list[str]) -> str:
    return "\n".join("  " + " ".join(s.split())[:140] for s in statements)


async def _counted(world, path: str, **params):
    # Warm the other request caches before measuring. Membership is read from PostgreSQL on every request.
    await world.client.get(path, params=params, headers=world.headers)
    with StatementCounter(world.engine) as counter:
        response = await world.client.get(path, params=params, headers=world.headers)
    return response, counter.statements


async def test_seeded_world_is_what_the_counts_assume(world):
    from app.models.postgres import TestCase, TestRun

    async with world.sessions() as db:
        runs = await db.scalar(
            select(func.count(TestRun.id)).where(TestRun.project_id == world.project_id)
        )
        cases = await db.scalar(
            select(func.count(TestCase.id)).where(
                TestCase.test_run_id == world.large_run
            )
        )
    assert runs == RUNS + 1
    assert cases == LARGE_RUN_CASES


async def test_run_list_statement_count_does_not_grow_with_the_page(world):
    small, small_statements = await _counted(
        world, "/api/v1/runs", project_id=str(world.project_id), size=5
    )
    large, large_statements = await _counted(
        world, "/api/v1/runs", project_id=str(world.project_id), size=50
    )
    assert small.status_code == 200, small.text[:300]
    assert large.status_code == 200, large.text[:300]
    small_body, large_body = small.json(), large.json()
    # Positive data: real pages of the seeded runs, enriched by the batched lookups.
    assert small_body["total"] == large_body["total"] == RUNS + 1
    assert len(small_body["items"]) == 5
    assert len(large_body["items"]) == 50
    assert {item["project_name"] for item in large_body["items"]} == {
        f"perf-runs-{world.tag}"
    }
    assert sum(1 for item in large_body["items"] if item["release_name"]) >= 30
    assert all(item["run_seq"] for item in large_body["items"])
    assert all(
        set(item["suite_names"] or ()) <= set(SUITES) and item["suite_names"]
        for item in large_body["items"]
    )
    assert len(large_statements) == len(small_statements), (
        f"GET /runs ran {len(small_statements)} statements for 5 runs and {len(large_statements)} for 50: "
        "a lookup runs per run (N+1)"
    )
    assert len(large_statements) == LIST_STATEMENTS, _shape(large_statements)


async def test_run_detail_statement_count_is_pinned(world):
    response, statements = await _counted(world, f"/api/v1/runs/{world.small_run}")
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    assert body["id"] == str(world.small_run)
    assert body["release_id"] == str(world.releases[0])
    assert body["run_seq"] and body["project_name"] == f"perf-runs-{world.tag}"
    assert sorted(body["suite_names"]) == sorted(SUITES)
    assert len(statements) == DETAIL_STATEMENTS, _shape(statements)


async def test_run_tests_statement_count_does_not_grow_with_the_page(world):
    path = f"/api/v1/runs/{world.large_run}/tests"
    small, small_statements = await _counted(world, path, size=5)
    large, large_statements = await _counted(world, path, size=50)
    assert small.status_code == 200, small.text[:300]
    assert large.status_code == 200, large.text[:300]
    assert small.json()["total"] == large.json()["total"] == LARGE_RUN_CASES
    assert len(small.json()["items"]) == 5
    items = large.json()["items"]
    assert len(items) == 50
    # The page is a summary: the failure text and stack stay on the case detail, not x200 rows x12 polls/min.
    assert any(item["status"] == "FAILED" for item in items)
    heavy = {"error_message", "stack_trace"} & set().union(
        *(item.keys() for item in items)
    )
    assert not heavy, f"run tests page items carry heavy fields: {sorted(heavy)}"
    assert len(large_statements) == len(small_statements), (
        f"GET /runs/{{id}}/tests ran {len(small_statements)} statements for 5 cases and "
        f"{len(large_statements)} for 50: a lookup runs per case (N+1)"
    )
    assert len(large_statements) == TESTS_STATEMENTS, _shape(large_statements)


@pytest.mark.parametrize(
    ("path_kind", "size", "table"),
    [("list", 501, "test_runs"), ("tests", 201, "test_cases")],
)
async def test_size_above_the_bound_is_422_before_any_row_read(
    world, path_kind, size, table
):
    path = (
        "/api/v1/runs"
        if path_kind == "list"
        else f"/api/v1/runs/{world.large_run}/tests"
    )
    params = {
        "size": size,
        **({"project_id": str(world.project_id)} if path_kind == "list" else {}),
    }
    response, statements = await _counted(world, path, **params)
    assert response.status_code == 422, response.text[:300]
    assert any(
        error["loc"][-1] == "size" and error["type"] == "less_than_equal"
        for error in response.json()["detail"]
    ), response.text[:300]
    touched = [s for s in statements if table in s]
    assert not touched, f"a refused size={size} still read {table}: {touched}"
