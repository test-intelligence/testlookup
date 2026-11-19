"""The keyword search skips its COUNT when the page already fixes the total (GAP-C10).

``services/search_service.py`` ``search_test_cases_query``: for an unanchored term the COUNT is a second full
evaluation of the predicate, so a short page reports ``offset + len(rows)`` and runs no COUNT.
``tests/integration/test_search_count_shortcut_postgres.py`` proves the shortcut's total equals the COUNT it
replaces; nothing proved the COUNT is actually *skipped*. A refactor that always counts passes every other
test and doubles the cost of every search.

Through ``GET /api/v1/search`` (keyword mode) on real PostgreSQL, counting statements on the engine:

* a short page (7 matches, ``size=20``) runs exactly one statement on ``test_cases`` and no ``SELECT count(``;
* a full page (``size=5``) runs exactly two: the page and one COUNT;
* both report ``total == 7``; ``size=101`` is a 422 that never reads ``test_cases``.

Seeded through ``create_run_from_payload`` + ``ingest_test_results``. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (skips only without it). One raising cleanup.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from tests.integration.defect_w06_world import StatementCounter

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

MATCHES = 7


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
            "users": await db.scalar(
                text("SELECT count(*) FROM users WHERE id = ANY(:u)"), {"u": user_ids}
            ),
        }
    leaked = {table: n for table, n in leftovers.items() if n}
    if errors or leaked:
        raise AssertionError(
            f"perf-search cleanup failed: errors={errors} leftovers={leaked}"
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
    from app.models.postgres import Project, ProjectMember, User, UserRole
    from app.services.ingestion_pipeline import (
        create_run_from_payload,
        ingest_test_results,
    )

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

    tag = "ps" + uuid.uuid4().hex[:8]
    term = f"perfterm{tag}"
    project_id, member_id = uuid.uuid4(), uuid.uuid4()
    client = None
    try:
        async with sessions.begin() as db:
            db.add(
                Project(
                    id=project_id, name=f"perf-search-{tag}", slug=f"perf-search-{tag}"
                )
            )
            db.add(
                User(
                    id=member_id,
                    email=f"perf-search-{tag}@example.test",
                    username=f"perf_search_{tag}",
                    full_name="Perf search member",
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
        results = [
            {
                "test_name": f"test_{term}_{n}",
                "class_name": "perf.Search",
                "suite_name": "perf-search",
                "status": "failed",
                "error_message": "AssertionError: perf search fixture",
            }
            for n in range(MATCHES)
        ] + [
            {
                "test_name": f"test_other_{n}",
                "class_name": "perf.Search",
                "suite_name": "perf-search",
                "status": "passed",
            }
            for n in range(3)
        ]
        async with sessions.begin() as db:
            run = await create_run_from_payload(
                db,
                project_id=str(project_id),
                build_number=f"{tag}-1",
                ingestion_source="sdk",
                ci_provider="github_actions",
                ci_repo="example/perf",
                ci_run_url=f"https://ci.example.test/{tag}",
            )
            assert await ingest_test_results(db, run, results) == len(results)

        client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )
        headers = {"Authorization": f"Bearer {create_access_token(str(member_id))}"}
        yield SimpleNamespace(
            engine=engine,
            client=client,
            headers=headers,
            project_id=project_id,
            term=term,
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


async def _search(world, size: int):
    params = {"q": world.term, "project_id": str(world.project_id), "size": size}
    # Unmeasured first: the membership lookup is cached in Redis, so the measured call starts warm.
    await world.client.get("/api/v1/search", params=params, headers=world.headers)
    with StatementCounter(world.engine) as counter:
        response = await world.client.get(
            "/api/v1/search", params=params, headers=world.headers
        )
    on_cases = [s for s in counter.statements if "test_cases" in s]
    counts = [
        s for s in on_cases if " ".join(s.split()).upper().startswith("SELECT COUNT(")
    ]
    return response, on_cases, counts


def _shape(statements: list[str]) -> str:
    return "\n".join("  " + " ".join(s.split())[:140] for s in statements)


async def test_short_keyword_page_skips_the_count(world):
    response, on_cases, counts = await _search(world, size=20)
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    assert body["search_type"] == "keyword"
    assert body["total"] == MATCHES
    assert len(body["items"]) == MATCHES
    assert all(world.term in item["test_name"] for item in body["items"])
    assert not counts, (
        f"a short page ({MATCHES} of 20) still ran the COUNT:\n{_shape(counts)}"
    )
    assert len(on_cases) == 1, f"expected the page statement only:\n{_shape(on_cases)}"


async def test_full_keyword_page_runs_exactly_one_count(world):
    response, on_cases, counts = await _search(world, size=5)
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    assert body["total"] == MATCHES
    assert len(body["items"]) == 5
    assert body["pages"] == 2
    assert len(counts) == 1, f"a full page must run one COUNT:\n{_shape(on_cases)}"
    assert len(on_cases) == 2, (
        f"expected the page statement and one COUNT:\n{_shape(on_cases)}"
    )


async def test_size_above_the_bound_is_422_before_any_row_read(world):
    params = {"q": world.term, "project_id": str(world.project_id), "size": 101}
    with StatementCounter(world.engine) as counter:
        response = await world.client.get(
            "/api/v1/search", params=params, headers=world.headers
        )
    assert response.status_code == 422, response.text[:300]
    assert any(
        error["loc"][-1] == "size" and error["type"] == "less_than_equal"
        for error in response.json()["detail"]
    ), response.text[:300]
    touched = [s for s in counter.statements if "test_cases" in s]
    assert not touched, f"a refused size=101 still read test_cases:\n{_shape(touched)}"
