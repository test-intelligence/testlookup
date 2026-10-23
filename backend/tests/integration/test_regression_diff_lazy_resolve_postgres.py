"""Real PostgreSQL proof of W10 FAIL-09: the Changes tab's lazy resolve stores one row, and a second view none.

``routers/runs.get_regression_diff`` runs for real on a database migrated to
head, with the real ``commit_attribution`` router helper, the real
``resolve_commit_range`` (its short read session, its GitHub calls with no
session open, its upsert on the helper's write session) and the real diff. The
project's GitHub integration is a real row on a GHE base; its PAT comes through
``secret_service.read_secret`` (patched to a dict: encryption is not under
test) and the ``github_checks`` gate is patched on (the flag's own seeding is
W19's). Every ``httpx`` client of the connector goes to the shared contract
fake. What only PostgreSQL shows:

* the first view stores ONE ``run_commit_ranges`` row through the fake (the
  compare and the commit details on the GHE base, with the project's PAT) and
  the diff lists those commits with links on the GHE host;
* a second view writes nothing: the row count, ``resolved_at`` and the fake's
  request log are unchanged;
* a project whose range is unavailable is resolved once, then left alone for
  the 6 h cooldown.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import os
import socket
import types
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.fakes.fake_integrations import FakeState, create_app, demo_sha

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    from app.core.config import settings
    from app.models.postgres import GitHubIntegration, Project, TestRun
    from app.routers import commit_attribution as commit_router
    from app.services import commit_attribution_service as cas
    from app.services import github_checks_service, secret_service, url_safety

    engine = create_async_engine(_dsn(), pool_size=5, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    tag = uuid.uuid4().hex[:8]
    with_pat, without_pat = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    runs: dict[str, uuid.UUID] = {}
    async with sessions.begin() as db:
        for pid, label in ((with_pat, "pay"), (without_pat, "nopat")):
            db.add(Project(id=pid, name=f"changes-{label}-{tag}", slug=f"changes-{label}-{tag}", is_active=True))
        await db.flush()
        for pid in (with_pat, without_pat):
            db.add(GitHubIntegration(project_id=pid, enabled=True, repo_owner="acme", repo_name="payments",
                                     api_base_url="https://ghe.fake.test/api/v3", has_pat=pid == with_pat))
            green, failing = uuid.uuid4(), uuid.uuid4()
            runs[f"{pid}:green"], runs[f"{pid}:failing"] = green, failing
            db.add(TestRun(id=green, project_id=pid, build_number=f"g-{tag}", branch="main", status="PASSED",
                           total_tests=1, passed_tests=1, failed_tests=0, broken_tests=0, pass_rate=100.0,
                           commit_hash=demo_sha("acme/payments", 0), ci_repo="acme/payments",
                           created_at=now - timedelta(hours=3), end_time=now - timedelta(hours=3)))
            db.add(TestRun(id=failing, project_id=pid, build_number=f"f-{tag}", branch="main", status="FAILED",
                           total_tests=1, passed_tests=0, failed_tests=1, broken_tests=0, pass_rate=0.0,
                           commit_hash=demo_sha("acme/payments", 3), ci_repo="acme/payments",
                           created_at=now - timedelta(hours=1), end_time=now - timedelta(hours=1)))

    fake = FakeState()
    real_async_client = httpx.AsyncClient
    proxy = types.SimpleNamespace(**{k: getattr(httpx, k) for k in dir(httpx) if not k.startswith("_")})
    proxy.AsyncClient = lambda *a, **k: real_async_client(transport=httpx.ASGITransport(app=create_app(fake)))

    async def read_secret(_db, scope, key):
        if scope == github_checks_service.SECRET_SCOPE and key == f"project:{with_pat}:pat":
            return "fake-gh-pat"
        return None

    def resolves(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("140.82.112.5", 443))]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)
    monkeypatch.setattr(cas, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(commit_router, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(cas, "httpx", proxy)
    monkeypatch.setattr(cas, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(github_checks_service, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(secret_service, "read_secret", read_secret)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", resolves)

    async def view(run_id):
        from app.routers.runs import get_regression_diff

        async with sessions() as db:
            return await get_regression_diff(run_id=run_id, db=db, _=None)

    async def ranges(project_id):
        async with sessions() as db:
            return (await db.execute(
                select(cas.RunCommitRange.source, cas.RunCommitRange.resolved_at,
                       cas.RunCommitRange.commits).where(cas.RunCommitRange.project_id == project_id)
            )).all()

    try:
        yield types.SimpleNamespace(with_pat=with_pat, without_pat=without_pat, runs=runs, fake=fake,
                                    view=view, ranges=ranges, tag=tag)
    finally:
        monkeypatch.undo()
        async with engine.begin() as conn:
            for pid in (with_pat, without_pat):
                await conn.execute(text("DELETE FROM run_commit_ranges WHERE project_id = :p"), {"p": pid})
                await conn.execute(text("DELETE FROM test_runs WHERE project_id = :p"), {"p": pid})
                await conn.execute(text("DELETE FROM github_integrations WHERE project_id = :p"), {"p": pid})
                await conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
        await engine.dispose()


async def test_the_first_view_stores_one_range_and_the_second_stores_none(world):
    failing = world.runs[f"{world.with_pat}:failing"]

    first = await world.view(failing)

    stored = await world.ranges(world.with_pat)
    assert len(stored) == 1 and stored[0].source == "connector"
    shas = [demo_sha("acme/payments", n) for n in (1, 2, 3)]
    assert [c["sha"] for c in stored[0].commits] == shas
    assert [c["full_sha"] for c in first["commit_range"]] == shas
    assert [c["commit_url"] for c in first["commit_range"]] == [
        f"https://ghe.fake.test/acme/payments/commit/{sha}" for sha in shas
    ]
    status = first["commit_range_status"]
    assert (status["available"], status["reason"], status["base_build_number"]) == (True, None, f"g-{world.tag}")
    github = [r for r in world.fake.requests if r["service"] == "github"]
    assert github and {(r["base"], r["auth"]) for r in github} == {("ghe", "ok")}
    assert github[0]["path"] == (
        f"/api/v3/repos/acme/payments/compare/{demo_sha('acme/payments', 0)}...{demo_sha('acme/payments', 3)}"
    )
    calls = len(world.fake.requests)

    second = await world.view(failing)

    again = await world.ranges(world.with_pat)
    assert len(again) == 1 and again[0].resolved_at == stored[0].resolved_at
    assert len(world.fake.requests) == calls, "the second view called GitHub again"
    assert second["commit_range"] == first["commit_range"]


async def test_an_unavailable_range_is_resolved_once_then_left_for_the_cooldown(world):
    failing = world.runs[f"{world.without_pat}:failing"]

    first = await world.view(failing)

    stored = await world.ranges(world.without_pat)
    assert len(stored) == 1 and stored[0].source == "unavailable"
    assert first["commit_range"] == []
    assert first["commit_range_status"]["reason"] == "no_token"

    second = await world.view(failing)

    again = await world.ranges(world.without_pat)
    assert len(again) == 1 and again[0].resolved_at == stored[0].resolved_at, "a GET wrote on every view"
    assert second["commit_range_status"]["reason"] == "no_token"
