"""Real PostgreSQL proof of W10 FAIL-09: the Changes tab's lazy resolve stores one row, and a second view none.

``routers/runs.get_regression_diff`` runs for real on a database migrated to
head, with the real ``commit_attribution`` router helper, the real
``resolve_commit_range`` (its short read session, its GitHub calls with no
session open, its upsert on the helper's write session) and the real diff. The
projects' GitHub integrations are real rows on a GHE base; the PATs come
through ``secret_service.read_secret`` (patched to a dict: encryption is not
under test) and the ``github_checks`` gate is a switch the test opens and
shuts (the flag's own seeding is W19's). Every ``httpx`` client of the
connector goes to the shared contract fake. What only PostgreSQL shows:

* the first view stores ONE ``run_commit_ranges`` row through the fake (the
  compare and the commit details on the GHE base, with the project's PAT) and
  the diff lists those commits with links on the GHE host;
* a second view writes nothing: the row count, ``resolved_at`` and the fake's
  request log are unchanged;
* (follow-up, found in the local demo) a GET never persists a gated outcome:
  a view while the target is unavailable (no token, the gate shut) stores no
  row; a row written while the gate was shut -- by finalize's resolve, or with
  no head commit at all -- is resolved by the first view after the gate opens
  (commits listed, not ``no_baseline_commit`` for 6 h); a row that reached
  the connector (a fetch failure) keeps its 6 h cooldown.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head.
"""
from __future__ import annotations

import os
import socket
import types
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.fakes.fake_integrations import FakeState, create_app, demo_sha

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

LABELS = ("pay", "nopat", "stale", "headless", "failed")
SHAS = [demo_sha("acme/payments", n) for n in (1, 2, 3)]


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
    projects = {label: uuid.uuid4() for label in LABELS}
    with_pat = {pid for label, pid in projects.items() if label != "nopat"}
    now = datetime.now(timezone.utc)
    runs: dict[str, uuid.UUID] = {}
    async with sessions.begin() as db:
        for label, pid in projects.items():
            db.add(Project(id=pid, name=f"changes-{label}-{tag}", slug=f"changes-{label}-{tag}", is_active=True))
        await db.flush()
        for label, pid in projects.items():
            db.add(GitHubIntegration(project_id=pid, enabled=True, repo_owner="acme", repo_name="payments",
                                     api_base_url="https://ghe.fake.test/api/v3", has_pat=pid in with_pat))
            green, failing = uuid.uuid4(), uuid.uuid4()
            runs[f"{label}:green"], runs[f"{label}:failing"] = green, failing
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
    gate = {"open": True}

    async def read_secret(_db, scope, key):
        if scope == github_checks_service.SECRET_SCOPE and any(key == f"project:{p}:pat" for p in with_pat):
            return "fake-gh-pat"
        return None

    async def post_allowed(_db=None, *, project_id=None, **_kw):
        return gate["open"]

    def resolves(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("140.82.112.5", 443))]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", sessions)
    monkeypatch.setattr(cas, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(commit_router, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(cas, "httpx", proxy)
    monkeypatch.setattr(cas, "_post_allowed", post_allowed)
    monkeypatch.setattr(github_checks_service, "_post_allowed", post_allowed)
    monkeypatch.setattr(secret_service, "read_secret", read_secret)
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", resolves)

    async def view(label):
        from app.routers.runs import get_regression_diff

        async with sessions() as db:
            return await get_regression_diff(run_id=runs[f"{label}:failing"], db=db, _=None)

    async def ranges(label):
        async with sessions() as db:
            return (await db.execute(
                select(cas.RunCommitRange.source, cas.RunCommitRange.resolved_at, cas.RunCommitRange.commits,
                       cas.RunCommitRange.base_commit, cas.RunCommitRange.head_commit)
                .where(cas.RunCommitRange.project_id == projects[label])
            )).all()

    async def finalize_resolve(label):
        """What finalize's isolated step does: the real resolve on its own session."""
        async with sessions() as db:
            await cas.resolve_commit_range(db, runs[f"{label}:failing"])
            await db.commit()

    async def plant(label, **columns):
        async with sessions.begin() as db:
            await db.execute(text(
                "INSERT INTO run_commit_ranges (id, run_id, project_id, base_commit, head_commit, source, "
                "base_source, commits, resolved_at) VALUES (:id, :run, :p, :base, :head, 'unavailable', "
                ":base_source, CAST('[]' AS jsonb), :at)"
            ), {"id": uuid.uuid4(), "run": runs[f"{label}:failing"], "p": projects[label], **columns})

    try:
        yield types.SimpleNamespace(projects=projects, runs=runs, fake=fake, gate=gate, view=view, ranges=ranges,
                                    finalize_resolve=finalize_resolve, plant=plant, tag=tag, now=now)
    finally:
        monkeypatch.undo()
        async with engine.begin() as conn:
            for pid in projects.values():
                await conn.execute(text("DELETE FROM run_commit_ranges WHERE project_id = :p"), {"p": pid})
                await conn.execute(text("DELETE FROM test_runs WHERE project_id = :p"), {"p": pid})
                await conn.execute(text("DELETE FROM github_integrations WHERE project_id = :p"), {"p": pid})
                await conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
        await engine.dispose()


def _github_calls(world) -> list[dict]:
    return [r for r in world.fake.requests if r["service"] == "github"]


async def test_the_first_view_stores_one_range_and_the_second_stores_none(world):
    first = await world.view("pay")

    stored = await world.ranges("pay")
    assert len(stored) == 1 and stored[0].source == "connector"
    assert [c["sha"] for c in stored[0].commits] == SHAS
    assert [c["full_sha"] for c in first["commit_range"]] == SHAS
    assert [c["commit_url"] for c in first["commit_range"]] == [
        f"https://ghe.fake.test/acme/payments/commit/{sha}" for sha in SHAS
    ]
    status = first["commit_range_status"]
    assert (status["available"], status["reason"], status["base_build_number"]) == (True, None, f"g-{world.tag}")
    github = _github_calls(world)
    assert github and {(r["base"], r["auth"]) for r in github} == {("ghe", "ok")}
    assert github[0]["path"] == (
        f"/api/v3/repos/acme/payments/compare/{demo_sha('acme/payments', 0)}...{demo_sha('acme/payments', 3)}"
    )
    calls = len(world.fake.requests)

    second = await world.view("pay")

    again = await world.ranges("pay")
    assert len(again) == 1 and again[0].resolved_at == stored[0].resolved_at
    assert len(world.fake.requests) == calls, "the second view called GitHub again"
    assert second["commit_range"] == first["commit_range"]


async def test_a_view_while_the_target_is_unavailable_stores_no_row(world):
    """(a) No token stored, then a shut gate: the view says why and writes nothing."""
    calls = len(world.fake.requests)

    no_token = await world.view("nopat")
    world.gate["open"] = False
    try:
        gated = await world.view("stale")
    finally:
        world.gate["open"] = True

    assert no_token["commit_range_status"]["reason"] == "no_token"
    assert gated["commit_range_status"]["reason"] == "github_checks_off"
    assert await world.ranges("nopat") == [] and await world.ranges("stale") == [], "a GET persisted a gated outcome"
    assert len(world.fake.requests) == calls


async def test_a_row_written_while_the_gate_was_shut_is_resolved_once_it_opens(world):
    """(b) The demo's sequence: finalize resolved while ``github_checks`` was off
    (an ``unavailable`` row, no base commit); the admin turns the gate on; the
    next view lists the commits instead of ``no_baseline_commit`` for 6 h. The
    same for a row recorded with no head commit at all."""
    world.gate["open"] = False
    try:
        await world.finalize_resolve("stale")
    finally:
        world.gate["open"] = True
    (gated,) = await world.ranges("stale")
    assert (gated.source, gated.base_commit) == ("unavailable", None)
    await world.plant("headless", base=None, head=None, base_source="unavailable", at=world.now)

    for label in ("stale", "headless"):
        diff = await world.view(label)

        (stored,) = await world.ranges(label)
        assert stored.source == "connector", label
        assert [c["sha"] for c in stored.commits] == SHAS
        assert diff["commit_range_status"]["reason"] is None, label
        assert [c["full_sha"] for c in diff["commit_range"]] == SHAS


async def test_a_connector_failure_keeps_its_cooldown(world):
    """(c) The row reached the connector (a base commit was anchored) and the
    fetch failed: it is not resolved again within 6 h, and says so."""
    planted_at = world.now - timedelta(hours=1)
    await world.plant("failed", base=demo_sha("acme/payments", 0), head=demo_sha("acme/payments", 3),
                      base_source="green_baseline", at=planted_at)
    calls = len(world.fake.requests)

    diff = await world.view("failed")
    await world.view("failed")

    (stored,) = await world.ranges("failed")
    assert stored.source == "unavailable" and stored.resolved_at == planted_at
    assert len(world.fake.requests) == calls, "a failed range was fetched again within its cooldown"
    assert diff["commit_range_status"]["reason"] == "fetch_failed"
