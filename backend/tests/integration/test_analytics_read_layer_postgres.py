"""VIZ-209 through the real app, on real PostgreSQL and real Redis.

The unit twin (``tests/regression/test_analytics_read_layer.py``) pins the
rules. Only a real server can answer these:

* a second identical request is served from Redis with the SAME ETag, and
  ``If-None-Match`` really produces a 304 with no body;
* a VIZ-212 epoch bump really makes the next request a miss with a new ETag;
* two callers with different accessible-project sets never share an entry --
  or an ETag -- on an all-projects query;
* Redis being unreachable falls through to the database rather than 500;
* ``statement_timeout`` really cancels a long query (SQLSTATE 57014), the
  answer is a 503 ``analytics_timeout`` with ``Retry-After``, and the very next
  request on THAT SAME connection succeeds;
* ``SET LOCAL`` really is local: the timeout is visible inside the request and
  gone from the pooled connection afterwards -- which is what keeps it off
  ingestion, whose sessions come from the same pool;
* the per-principal limit really returns 429 with ``Retry-After``;
* ``analytics_query_duration_seconds`` is really observed on the served path.

The pool-bound tests run on their own engine with ``pool_size=1,
max_overflow=0``: "the next request" is then necessarily the same connection,
not merely likely to be.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (migrated to head) and ``REDIS_URL``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.test_analytics_scope_postgres import _env
from tests.integration.test_analytics_scope_postgres import world as _seeded_world

#: The seeded world as this module's own (module-scoped) fixture.
world = _seeded_world

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

COVERAGE = "/api/v1/analytics/coverage"
TOP_FAILING = "/api/v1/analytics/top-failing"


def _hist_count(metric, **labels) -> float:
    child = metric.labels(**labels) if labels else metric
    return sum(b.get() for b in child._buckets)


async def _real_redis(world) -> None:
    """Skip unless the world is talking to a real Redis server."""
    from app.db.redis_client import get_redis

    client = get_redis()
    if type(client).__module__.startswith("fakeredis"):
        pytest.skip("REDIS_URL is not configured (the world fell back to fakeredis)")
    await client.ping()


@pytest_asyncio.fixture(loop_scope="module")
async def solo(world):
    """A one-connection pool behind the app, so "the next request" is provably
    the same PostgreSQL backend."""
    from app.db.postgres import get_db
    from app.main import app

    engine = create_async_engine(_env("TESTLOOKUP_POSTGRES_TEST_DSN"), pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _get_db
    try:
        yield SimpleNamespace(sessions=sessions, engine=engine)
    finally:
        if previous is not None:
            app.dependency_overrides[get_db] = previous
        else:
            app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


# ── cache, ETag, 304 ────────────────────────────────────────────────────────


async def test_the_second_identical_request_is_a_hit_with_the_same_etag(world) -> None:
    await _real_redis(world)
    params = {"project_id": str(world.p1), "days": 31}

    first = await world.client.get(COVERAGE, params=params, headers=world.admin)
    second = await world.client.get(COVERAGE, params=params, headers=world.admin)

    assert first.status_code == 200, first.text
    assert first.headers["X-Analytics-Cache"] == "miss"
    assert second.headers["X-Analytics-Cache"] == "hit"
    assert first.headers["ETag"] and second.headers["ETag"] == first.headers["ETag"]
    assert second.content == first.content
    assert second.json()["meta"]["scope"]["window"]["days"] == 31


async def test_if_none_match_is_a_304_with_no_body(world) -> None:
    await _real_redis(world)
    params = {"project_id": str(world.p1), "days": 32}

    first = await world.client.get(COVERAGE, params=params, headers=world.admin)
    etag = first.headers["ETag"]
    again = await world.client.get(
        COVERAGE, params=params, headers={**world.admin, "If-None-Match": etag}
    )

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["ETag"] == etag


async def test_a_weak_validator_matches_too(world) -> None:
    """A proxy may weaken the tag; RFC 9110 compares If-None-Match weakly."""
    await _real_redis(world)
    params = {"project_id": str(world.p1), "days": 33}
    etag = (await world.client.get(COVERAGE, params=params, headers=world.admin)).headers["ETag"]
    again = await world.client.get(
        COVERAGE, params=params, headers={**world.admin, "If-None-Match": f"W/{etag}"}
    )
    assert again.status_code == 304


async def test_an_epoch_bump_is_a_miss_with_a_new_etag(world) -> None:
    """VIZ-212's invalidation, from the reader's side."""
    from app.services.cache_service import bump_analytics_epoch

    await _real_redis(world)
    params = {"project_id": str(world.p1), "days": 34}

    first = await world.client.get(COVERAGE, params=params, headers=world.admin)
    assert (await world.client.get(COVERAGE, params=params, headers=world.admin)).headers[
        "X-Analytics-Cache"
    ] == "hit"

    await bump_analytics_epoch(str(world.p1))

    after = await world.client.get(COVERAGE, params=params, headers=world.admin)
    assert after.status_code == 200
    assert after.headers["X-Analytics-Cache"] == "miss"
    assert after.headers["ETag"] != first.headers["ETag"], (
        "the client still holds the pre-mutation tag: a 304 against it would "
        "serve numbers the mutation changed"
    )
    stale = await world.client.get(
        COVERAGE, params=params, headers={**world.admin, "If-None-Match": first.headers["ETag"]}
    )
    assert stale.status_code == 200


async def test_the_etag_varies_by_the_callers_accessible_projects(world) -> None:
    """An all-projects answer IS the caller's accessible set. The admin sees
    every project, the member sees one; sharing an entry (or a validator)
    between them would leak one project's numbers into the other's report."""
    await _real_redis(world)
    params = {"days": 35}

    admin = await world.client.get(COVERAGE, params=params, headers=world.admin)
    member = await world.client.get(COVERAGE, params=params, headers=world.member)

    assert admin.status_code == 200 and member.status_code == 200
    assert member.headers["X-Analytics-Cache"] == "miss", (
        "the member was served the admin's cached all-projects payload"
    )
    assert member.headers["ETag"] != admin.headers["ETag"]

    revalidated = await world.client.get(
        COVERAGE, params=params, headers={**world.member, "If-None-Match": admin.headers["ETag"]}
    )
    assert revalidated.status_code == 200, (
        "the member was told 'not modified' about the admin's snapshot"
    )


async def test_one_projects_key_space_is_its_own(world) -> None:
    """Keys are per project, so one project's entries (or their absence)
    cannot answer for another."""
    await _real_redis(world)
    params = {"days": 36}
    p1 = await world.client.get(COVERAGE, params={**params, "project_id": str(world.p1)}, headers=world.admin)
    p2 = await world.client.get(COVERAGE, params={**params, "project_id": str(world.p2)}, headers=world.admin)
    assert p1.headers["ETag"] != p2.headers["ETag"]
    assert p2.headers["X-Analytics-Cache"] == "miss"

    from app.db.redis_client import get_redis

    keys = [key async for key in get_redis().scan_iter(match=f"analytics:coverage:{world.p1}:*")]
    assert keys, "the entry is not namespaced by project"


async def test_redis_down_is_served_from_the_database(world, monkeypatch) -> None:
    """Never a 500, and never a value whose epoch cannot be read."""

    def _down():
        raise ConnectionError("redis is unreachable")

    monkeypatch.setattr("app.db.redis_client.get_redis", _down)
    params = {"project_id": str(world.p1), "days": 37}

    first = await world.client.get(COVERAGE, params=params, headers=world.admin)
    second = await world.client.get(COVERAGE, params=params, headers=world.admin)

    assert first.status_code == 200, first.text
    assert second.status_code == 200
    assert first.headers["X-Analytics-Cache"] == "miss"
    assert second.headers["X-Analytics-Cache"] == "miss"
    assert first.headers["ETag"] == second.headers["ETag"], (
        "the ETag is computed from the identity and the bytes, so it survives "
        "Redis being gone"
    )
    assert first.json()["summary"] == second.json()["summary"]


# ── statement timeout ───────────────────────────────────────────────────────


async def test_a_timeout_is_503_analytics_timeout_and_the_connection_survives(
    world, solo, monkeypatch
) -> None:
    from app.core import analytics_read_layer as layer
    from app.services import analytics_service

    real = analytics_service.coverage_stats
    calls = {"n": 0}

    async def _slow(db, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # A real query, cancelled by the real statement_timeout.
            await db.execute(text("SELECT pg_sleep(2)"))
        return await real(db, *args, **kwargs)

    monkeypatch.setattr(analytics_service, "coverage_stats", _slow)
    monkeypatch.setattr(layer, "ANALYTICS_STATEMENT_TIMEOUT_MS", 150)
    params = {"project_id": str(world.p1), "days": 38}

    refused = await world.client.get(COVERAGE, params=params, headers=world.admin)

    assert refused.status_code == 503, refused.text
    body = refused.json()
    assert body["code"] == "analytics_timeout"
    assert int(refused.headers["Retry-After"]) > 0
    assert "Retry" in body["message"] and body["request_id"]
    assert "Traceback" not in refused.text

    # The very next request, on the same (one-connection) pool.
    monkeypatch.setattr(layer, "ANALYTICS_STATEMENT_TIMEOUT_MS", 5000)
    healthy = await world.client.get(
        COVERAGE, params={**params, "days": 39}, headers=world.admin
    )
    assert healthy.status_code == 200, healthy.text
    assert calls["n"] == 2

    async with solo.sessions() as session:
        assert (await session.execute(text("SELECT 1"))).scalar() == 1


async def test_set_local_is_visible_inside_the_request_and_gone_after_it(
    world, solo, monkeypatch
) -> None:
    """The positive control and the leak check in one.

    Inside the request the session must really carry the analytics ceiling --
    a "no leak" test alone passes just as well when the timeout was never
    applied. Afterwards the SAME pooled connection must be back to the server
    default, which is what keeps the ceiling off ingestion: ingestion takes its
    sessions from this pool and must never inherit a 5 s limit.
    """
    from app.core import analytics_read_layer as layer
    from app.services import analytics_service

    monkeypatch.setattr(layer, "ANALYTICS_STATEMENT_TIMEOUT_MS", 4000)

    async def _probe(db, *args, **kwargs):
        seen = (await db.execute(text("SHOW statement_timeout"))).scalar()
        return {"summary": {"suite_count": 0, "seen_timeout": seen}, "suites": []}

    monkeypatch.setattr(analytics_service, "coverage_stats", _probe)

    response = await world.client.get(
        COVERAGE, params={"project_id": str(world.p1), "days": 40}, headers=world.admin
    )

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["seen_timeout"] == "4s", (
        "the analytics statement timeout never reached the request's session"
    )

    async with solo.sessions() as session:
        after = (await session.execute(text("SHOW statement_timeout"))).scalar()
    assert after == "0", (
        f"the statement timeout leaked to the pooled connection as {after!r}: "
        "SET LOCAL was replaced by a session-level SET, and the next user of "
        "this connection -- an ingestion batch, a Celery task -- inherits it"
    )


async def test_an_ingestion_session_on_the_same_pool_has_no_ceiling(
    world, solo, monkeypatch
) -> None:
    """Ingestion is not decorated, so it must see the server default even
    right after an analytics read used the same connection."""
    from app.core import analytics_read_layer as layer

    monkeypatch.setattr(layer, "ANALYTICS_STATEMENT_TIMEOUT_MS", 4000)
    read = await world.client.get(
        COVERAGE, params={"project_id": str(world.p1), "days": 41}, headers=world.admin
    )
    assert read.status_code == 200

    from app.models.postgres import LaunchStatus, TestRun

    run_id = uuid.uuid4()
    async with solo.sessions() as session:
        assert (await session.execute(text("SHOW statement_timeout"))).scalar() == "0"
        session.add(TestRun(
            id=run_id, project_id=world.p1, build_number=f"viz209-{run_id.hex[:8]}",
            status=LaunchStatus.PASSED, total_tests=1, passed_tests=1,
        ))
        await session.commit()
        assert (await session.execute(text("SHOW statement_timeout"))).scalar() == "0"
        await session.execute(text("DELETE FROM test_runs WHERE id = :id"), {"id": run_id})
        await session.commit()


# ── rate limit ──────────────────────────────────────────────────────────────


async def test_over_the_per_principal_limit_is_429_with_retry_after(
    world, monkeypatch
) -> None:
    from app.core import analytics_read_layer as layer

    monkeypatch.setattr(layer, "ANALYTICS_RATE_LIMIT", "2/minute")
    params = {"project_id": str(world.p1), "days": 42}

    codes = []
    for _ in range(3):
        response = await world.client.get(TOP_FAILING, params=params, headers=world.member)
        codes.append(response.status_code)

    assert codes[:2] == [200, 200], codes
    assert codes[2] == 429
    refused = await world.client.get(TOP_FAILING, params=params, headers=world.member)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0
    assert refused.json()["code"] == "rate_limited"

    # A different principal has its own budget.
    other = await world.client.get(TOP_FAILING, params=params, headers=world.admin)
    assert other.status_code == 200


# ── metrics ─────────────────────────────────────────────────────────────────


async def test_the_histogram_is_observed_on_the_served_path(world) -> None:
    """Declaration is not emission: this reads the value back after a real
    HTTP request, not after calling the emitter."""
    from app.core.metrics import analytics_query_duration_seconds

    params = {"project_id": str(world.p1), "days": 43}
    before_miss = _hist_count(analytics_query_duration_seconds, route=COVERAGE, outcome="miss")
    before_hit = _hist_count(analytics_query_duration_seconds, route=COVERAGE, outcome="hit")

    assert (await world.client.get(COVERAGE, params=params, headers=world.admin)).status_code == 200
    assert (await world.client.get(COVERAGE, params=params, headers=world.admin)).status_code == 200

    assert _hist_count(
        analytics_query_duration_seconds, route=COVERAGE, outcome="miss"
    ) == before_miss + 1
    assert _hist_count(
        analytics_query_duration_seconds, route=COVERAGE, outcome="hit"
    ) == before_hit + 1


# ── the limit is charged BEFORE the scope is resolved ──────────────────────
#
# Reviewers' finding: the limit was counted inside the handler, which FastAPI
# reaches only AFTER the scope dependency has run. Every 403, 404 and 422 was
# therefore free, unlimited database work -- 12 forbidden and 6 malformed
# requests cost a principal nothing and were served while it was over the
# limit. The gate dependency is ordered ahead of ``analytics_scope``.


async def test_a_forbidden_request_still_costs_the_principal(world, monkeypatch) -> None:
    from app.core import analytics_read_layer as layer

    monkeypatch.setattr(layer, "ANALYTICS_RATE_LIMIT", "3/minute")
    params = {"project_id": str(world.p1), "days": 44}

    codes = [
        (await world.client.get(COVERAGE, params=params, headers=world.outsider)).status_code
        for _ in range(5)
    ]
    assert codes[:3] == [403, 403, 403], codes
    assert codes[3:] == [429, 429], (
        f"refused requests are not counted: {codes} -- an outsider can run "
        "resolve_project_scope against PostgreSQL as often as it likes"
    )


async def test_a_malformed_request_is_counted_too(world, monkeypatch) -> None:
    from app.core import analytics_read_layer as layer

    monkeypatch.setattr(layer, "ANALYTICS_RATE_LIMIT", "4/minute")
    bad = {"project_id": str(world.p1), "days": 45, "release_id": "not-a-uuid"}

    codes = [
        (await world.client.get(COVERAGE, params=bad, headers=world.member)).status_code
        for _ in range(6)
    ]
    assert codes[:4] == [422, 422, 422, 422], codes
    assert set(codes[4:]) == {429}, codes

    over = await world.client.get(
        COVERAGE, params={"project_id": str(world.p1), "days": 45}, headers=world.member
    )
    assert over.status_code == 429, "a well-formed request slipped through a spent budget"


async def test_the_scopes_own_queries_run_under_the_analytics_timeout(
    world, solo, monkeypatch
) -> None:
    """``resolve_project_scope`` and the release ``IN`` lookup are the scope
    dependency's queries, and they used to run before any timeout was set."""
    from app.core import analytics_read_layer as layer
    from app.core import deps

    monkeypatch.setattr(layer, "ANALYTICS_STATEMENT_TIMEOUT_MS", 4000)
    seen: list = []
    original = deps.resolve_project_scope

    async def _watch(db, user, project_id=None, *args, **kwargs):
        seen.append((await db.execute(text("SHOW statement_timeout"))).scalar())
        return await original(db, user, project_id, *args, **kwargs)

    monkeypatch.setattr(deps, "resolve_project_scope", _watch)

    response = await world.client.get(
        COVERAGE, params={"project_id": str(world.p1), "days": 46}, headers=world.admin
    )
    assert response.status_code == 200, response.text
    assert seen == ["4s"], (
        f"the scope resolved with statement_timeout={seen!r}: its own queries "
        "were unbounded, and they are the ones a forbidden request pays for"
    )


# ── a cache hit does not claim it was generated now ────────────────────────


async def test_a_hit_restamps_generated_at_and_keeps_as_of(world, monkeypatch) -> None:
    """The reviewers' scenario exactly: read once, come back five minutes
    later inside the TTL, and see what the envelope claims."""
    from datetime import datetime, timedelta, timezone

    from app.services import analytics_meta

    await _real_redis(world)
    params = {"project_id": str(world.p1), "days": 47}

    miss = await world.client.get(COVERAGE, params=params, headers=world.admin)

    # The world freezes this module's clock; move it on, so "now" at the hit
    # is not "now" at the miss.
    later = analytics_meta.datetime.now(timezone.utc) + timedelta(minutes=5)

    class _Later(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return later.astimezone(tz) if tz is not None else later.replace(tzinfo=None)

    monkeypatch.setattr(analytics_meta, "datetime", _Later)
    hit = await world.client.get(COVERAGE, params=params, headers=world.admin)

    assert miss.headers["X-Analytics-Cache"] == "miss"
    assert hit.headers["X-Analytics-Cache"] == "hit"
    first, second = miss.json()["meta"], hit.json()["meta"]
    assert second["as_of"] == first["as_of"], (
        "as_of is the moment the NUMBERS were read; a hit must not move it"
    )
    assert second["as_of"] < second["generated_at"], (
        f"the cached body still claims generated_at={second['generated_at']} "
        f"for numbers read at {second['as_of']}"
    )

    # ...and the ETag is unchanged, so revalidation inside the TTL is a 304.
    assert hit.headers["ETag"] == miss.headers["ETag"], (
        "restamping generated_at invalidated the validator: every revalidation "
        "inside the TTL would now be a full payload"
    )
    revalidated = await world.client.get(
        COVERAGE, params=params, headers={**world.admin, "If-None-Match": miss.headers["ETag"]}
    )
    assert revalidated.status_code == 304 and revalidated.content == b""


# ── the cache identity is the resolved request ─────────────────────────────


async def test_the_two_group_by_orders_are_two_charts(world) -> None:
    """``group_by`` is order-significant: the first dimension is the x axis and
    the second keys the series, so the two orders are transposes. They shared
    one cache entry and one ETag."""
    await _real_redis(world)
    chart = "/api/v1/analytics/chart-data"
    base = [("project_id", str(world.p1)), ("days", "48"), ("metric", "executions")]

    forward = await world.client.get(
        chart, params=[*base, ("group_by", "status"), ("group_by", "environment")],
        headers=world.admin,
    )
    backward = await world.client.get(
        chart, params=[*base, ("group_by", "environment"), ("group_by", "status")],
        headers=world.admin,
    )

    assert forward.status_code == 200, forward.text
    assert backward.status_code == 200, backward.text
    assert backward.headers["X-Analytics-Cache"] == "miss", (
        "the transposed chart was served from the first chart's cache entry"
    )
    assert backward.headers["ETag"] != forward.headers["ETag"]
    assert backward.content != forward.content, (
        "status on the x axis keyed by environment, and its transpose, came "
        "back as the same bytes"
    )

    stale = await world.client.get(
        chart,
        params=[*base, ("group_by", "environment"), ("group_by", "status")],
        headers={**world.admin, "If-None-Match": forward.headers["ETag"]},
    )
    assert stale.status_code == 200, (
        "a client holding the first chart was told its transpose was 'not modified'"
    )


async def test_a_repeated_scalar_is_refused(world) -> None:
    """``?metric=failed&metric=passed`` binds LAST WINS but keyed SORTED, so a
    co-tenant sending the reverse order wrote the entry the first caller read."""
    chart = "/api/v1/analytics/chart-data"
    response = await world.client.get(
        chart,
        params=[("project_id", str(world.p1)), ("group_by", "day"),
                ("metric", "failed"), ("metric", "passed")],
        headers=world.admin,
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "repeated_parameter" and body["param"] == "metric"


async def test_an_ignored_query_parameter_is_not_a_second_cache_entry(world) -> None:
    """Keying on the RESOLVED request, not the query string: 25 spellings of a
    parameter the route does not answer were 25 entries for one payload."""
    await _real_redis(world)
    chart = "/api/v1/analytics/chart-data"
    base = [("project_id", str(world.p1)), ("days", "49"), ("metric", "executions"),
            ("group_by", "day")]

    first = await world.client.get(chart, params=base, headers=world.admin)
    junk = await world.client.get(chart, params=[*base, ("zzz", "7")], headers=world.admin)

    assert first.status_code == 200, first.text
    assert junk.headers["X-Analytics-Cache"] == "hit"
    assert junk.headers["ETag"] == first.headers["ETag"]


# ── headers ─────────────────────────────────────────────────────────────────


async def test_every_analytics_response_varies_on_the_credential(world) -> None:
    response = await world.client.get(
        COVERAGE, params={"project_id": str(world.p1), "days": 50}, headers=world.admin
    )
    assert response.status_code == 200, response.text
    assert response.headers["Vary"] == "Authorization, Cookie"
    assert "private" in response.headers["Cache-Control"]
