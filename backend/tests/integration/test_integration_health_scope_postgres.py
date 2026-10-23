"""Real PostgreSQL proof of W10 INT-04: the beat's per-project sweep, end to end, on the composite key.

The beat's body (``run_project_probes(prune=True)`` then
``persist_probe_results(prune=True)``) runs for real on a database migrated to
head (0223): the integrations are real rows, the read phase is the real one,
and the HTTP goes to the shared contract fake through ``httpx.ASGITransport``
(the PATs come through ``secret_service.read_secret``, patched to a dict: the
encryption is not under test). What only PostgreSQL shows:

* deployment-wide rows (``scope_key = ''``) and per-project rows of the SAME
  provider live side by side under the ``(provider, scope_key)`` primary key;
* a second sweep after one integration is disabled deletes that project's row
  in the same transaction and keeps every deployment-wide row;
* the key refuses a second ``(provider, '')`` row;
* the sweep's wall time over 20 projects (reported, bounded).

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import os
import socket
import time
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.fakes.fake_integrations import FakeState, create_app

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
    from app.models.postgres import GitHubIntegration, Project
    from app.services import github_checks_service, secret_service, url_safety
    from app.services import integration_probe_service as probes

    engine = create_async_engine(_dsn(), pool_size=5, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    tag = uuid.uuid4().hex[:8]
    projects = [uuid.uuid4() for _ in range(20)]
    pats: dict[str, str] = {}
    async with sessions.begin() as db:
        for n, pid in enumerate(projects):
            db.add(Project(id=pid, name=f"health-{tag}-{n:02d}", slug=f"health-{tag}-{n:02d}", is_active=True))
        await db.flush()
        for n, pid in enumerate(projects):
            ghe = n % 2 == 1
            db.add(GitHubIntegration(
                project_id=pid, enabled=True,
                repo_owner="acme", repo_name="checkout" if ghe else "payments",
                api_base_url="https://ghe.fake.test/api/v3" if ghe else "https://api.github.com",
                has_pat=True,
            ))
            pats[github_checks_service._secret_key(pid)] = "fake-gh-pat-2" if ghe else "fake-gh-pat"

    state = FakeState()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))

    async def read_secret(_db, scope, key):
        return pats.get(key) if scope == github_checks_service.SECRET_SCOPE else None

    def resolves(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("140.82.112.5", 443))]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(probes, "get_http_client", lambda: client)
    monkeypatch.setattr(secret_service, "read_secret", read_secret)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", resolves)

    async def rows():
        async with sessions() as db:
            result = await db.execute(text(
                "SELECT provider, scope_key, status FROM integration_health_checks "
                "WHERE scope_key = ANY(:scopes) OR provider LIKE :tag ORDER BY provider, scope_key"
            ), {"scopes": [str(p) for p in projects], "tag": f"%-{tag}"})
            return [tuple(r) for r in result]

    try:
        yield type("World", (), dict(
            sessions=sessions, projects=projects, tag=tag, state=state, rows=rows, probes=probes,
        ))
    finally:
        monkeypatch.undo()
        async with engine.begin() as conn:
            scopes = [str(p) for p in projects]
            await conn.execute(text("DELETE FROM integration_health_checks WHERE scope_key = ANY(:s) "
                                    "OR provider LIKE :t"), {"s": scopes, "t": f"%-{tag}"})
            await conn.execute(text("DELETE FROM integration_probe_results WHERE scope_key = ANY(:s) "
                                    "OR provider LIKE :t"), {"s": scopes, "t": f"%-{tag}"})
            await conn.execute(text("DELETE FROM github_integrations WHERE project_id = ANY(:p)"),
                               {"p": projects})
            await conn.execute(text("DELETE FROM projects WHERE id = ANY(:p)"), {"p": projects})
        await engine.dispose()


async def _sweep(world):
    """The beat's body (worker/tasks.py, G-B), with two deployment-wide rows of
    this test's own so a shared database's real rows are never touched."""
    probes = world.probes
    results = [
        probes.ProbeResult(f"alpha-{world.tag}", "healthy", 20, "deployment-wide"),
        probes.ProbeResult(f"beta-{world.tag}", "skipped", message="not configured"),
    ]
    project_results = await probes.run_project_probes(prune=True)
    results += project_results
    await probes.persist_probe_results(results, prune=True)
    return project_results


async def test_the_sweep_writes_global_and_project_rows_then_prunes_a_disabled_one(world):
    started = time.monotonic()
    first = await _sweep(world)
    elapsed = time.monotonic() - started
    print(f"\nW10 INT-04 beat sweep over {len(world.projects)} fake projects: {elapsed:.3f} s")
    assert elapsed < 30, "20 probes behind a gate of 5 should take seconds, not minutes"

    assert {r.status for r in first} == {"healthy"}, [(r.project_id, r.message) for r in first]
    bases = sorted({r["base"] for r in world.state.requests if r["service"] == "github"})
    assert bases == ["dotcom", "ghe"]
    rows = await world.rows()
    assert (f"alpha-{world.tag}", "", "healthy") in rows and (f"beta-{world.tag}", "", "skipped") in rows
    project_rows = [r for r in rows if r[1]]
    assert len(project_rows) == 20 and {r[0] for r in project_rows} == {"github"}

    disabled = world.projects[3]
    async with world.sessions.begin() as db:
        await db.execute(text("UPDATE github_integrations SET enabled = false WHERE project_id = :p"),
                         {"p": disabled})
    await _sweep(world)

    rows = await world.rows()
    assert ("github", str(disabled), "healthy") not in rows, "the disabled integration's row was kept"
    assert len([r for r in rows if r[1]]) == 19
    assert (f"alpha-{world.tag}", "", "healthy") in rows and (f"beta-{world.tag}", "", "skipped") in rows

    async with world.sessions() as db:
        history = (await db.execute(text(
            "SELECT count(*) FROM integration_probe_results WHERE scope_key = :s"), {"s": str(world.projects[0])}
        )).scalar_one()
    assert history == 2, "each sweep's probe of a project lands in that project's history"


async def test_the_composite_key_refuses_a_second_deployment_wide_row(world):
    provider = f"dup-{world.tag}"
    async with world.sessions.begin() as db:
        await db.execute(text(
            "INSERT INTO integration_health_checks (provider, scope_key, status) VALUES (:p, '', 'healthy')"
        ), {"p": provider})
        await db.execute(text(
            "INSERT INTO integration_health_checks (provider, scope_key, status) VALUES (:p, :s, 'healthy')"
        ), {"p": provider, "s": str(world.projects[0])})
    with pytest.raises(IntegrityError):
        async with world.sessions.begin() as db:
            await db.execute(text(
                "INSERT INTO integration_health_checks (provider, scope_key, status) VALUES (:p, '', 'down')"
            ), {"p": provider})
