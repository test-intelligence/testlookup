"""SRCH-05 search usage against a real PostgreSQL (and the retention class).

What a fake session cannot show:

1. **The one aggregate statement is right.** ``count(*) FILTER (...)``, the
   exact-zero rule (a lower-bound zero is not a zero), the fallback count and
   ``percentile_cont`` at 0.5 / 0.95 -- compared with the same linear
   interpolation computed here; the scope predicates (a pinned project, a
   member's ``= ANY(:ids)``, ADMIN's everything incl. All-Projects rows).
2. **No column can hold the query text.** Rows written from
   ``build_event(q=<term>)`` are read back column by column and the term is
   in none of them; the only text columns are the 64-hex hash and three
   closed vocabularies.
3. **The fixed 30-day window.** ``purge_expired_batch`` deletes only rows
   older than 30 days, in bounded batches, NULL-project rows included;
   ``retention_service.run_purge`` previews ``search_usage_rows`` for THE
   project and its execute deletes exactly those.

Every row is written under fresh projects and removed at the end. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (0227) and a reachable Mongo
(``run_purge`` reads it). To be listed in the ``postgres-integration`` job of
``.github/workflows/ci.yml``.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

#: A fixed clock: every window below is computed from it, never from the server's now().
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
TERM = "zq-sentinel-7 Checkout Timeout"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _NoStorage:
    async def list_objects(self, prefix, *, bucket=None):
        return []

    async def delete_prefix(self, prefix, *, bucket=None):
        return 0

    async def delete_object(self, key, *, bucket=None):
        return None


@pytest_asyncio.fixture(loop_scope="module")
async def env():
    from app.db import mongo as app_mongo
    from app.db import postgres as app_postgres
    from app.db.mongo import get_mongo_db

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    app_mongo.reset_mongo_client()  # bind Motor to this module's loop
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions, get_mongo_db()
    finally:
        await engine.dispose()
        app_mongo.reset_mongo_client()


async def _projects(sessions, n: int) -> list[uuid.UUID]:
    from app.models.postgres import Project

    ids = [uuid.uuid4() for _ in range(n)]
    async with sessions() as db:
        for pid in ids:
            tag = pid.hex[:10]
            db.add(Project(id=pid, name=f"srch05-{tag}", slug=f"srch05-{tag}", is_active=True))
        await db.commit()
    return ids


async def _events(sessions, rows: list[dict]) -> list[uuid.UUID]:
    """Each row: build_event's keywords (minus q) + ``age`` (a timedelta before NOW)."""
    from app.models.postgres import SearchQueryEvent
    from app.services.search_usage_service import build_event

    ids = []
    async with sessions() as db:
        for spec in rows:
            spec = dict(spec)
            age = spec.pop("age", timedelta(hours=1))
            kwargs = dict(
                q=spec.pop("q", TERM), word_count=3, filter_count=0, project_id=None, entity_scope="all",
                mode_requested="keyword", mode_used="keyword", fallback_reason=None, result_count=4,
                counts_exact=True, latency_ms=25, now=NOW - age,
            )
            kwargs.update(spec)
            event_id = uuid.uuid4()
            db.add(SearchQueryEvent(id=event_id, **build_event(**kwargs)))
            ids.append(event_id)
        await db.commit()
    return ids


async def _cleanup(sessions, projects, event_ids=()):
    async with sessions() as db:
        if event_ids:
            await db.execute(text("DELETE FROM search_query_events WHERE id = ANY(:ids)"), {"ids": list(event_ids)})
        for pid in projects:
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
        await db.commit()


def _percentile_cont(values: list[float], fraction: float) -> float:
    """PostgreSQL's percentile_cont: linear interpolation at fraction * (n - 1)."""
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


async def _summary(sessions, **kwargs):
    from app.services.search_usage_service import usage_summary

    async with sessions() as db:
        return await usage_summary(db, now=NOW, **kwargs)


async def test_rates_percentiles_and_the_scope(env):
    sessions, _ = env
    a, b, fresh = await _projects(sessions, 3)
    latencies = [10, 20, 30, 40, 1000]
    created = []
    try:
        created += await _events(sessions, [
            {"project_id": a, "result_count": 0},                                  # an exact zero
            {"project_id": a, "result_count": 4, "mode_requested": "semantic", "fallback_reason": "semantic_unavailable"},
            {"project_id": a, "result_count": 9},
        ])
        created += await _events(sessions, [
            {"project_id": b, "latency_ms": ms, "result_count": 0 if ms == 40 else 2, "counts_exact": ms != 40}
            for ms in latencies
        ])                                                                          # 40 ms: a LOWER-BOUND zero
        created += await _events(sessions, [{"project_id": None, "result_count": 0}])   # All Projects
        created += await _events(sessions, [{"project_id": a, "age": timedelta(days=3)}])  # outside "today"

        today = await _summary(sessions, project_id=a, allowed_project_ids=None, days=1)
        assert today["measured"] is True and today["searches"] == 3
        assert today["zero_result_searches"] == 1
        assert today["zero_result_rate"] == pytest.approx(1 / 3, abs=1e-4)
        assert today["fallback_searches"] == 1
        assert today["since"] == datetime(2026, 10, 24, tzinfo=timezone.utc)
        week = await _summary(sessions, project_id=a, allowed_project_ids=None, days=7)
        assert week["searches"] == 4, "days=7 reaches the 3-day-old row"

        tail = await _summary(sessions, project_id=b, allowed_project_ids=None, days=1)
        assert tail["searches"] == 5
        assert tail["zero_result_searches"] == 0, "a search that lost a source is not a zero"
        assert tail["zero_result_rate"] == 0.0
        assert tail["p50_latency_ms"] == pytest.approx(_percentile_cont(latencies, 0.5)) == 30
        assert tail["p95_latency_ms"] == pytest.approx(_percentile_cont(latencies, 0.95)) == pytest.approx(808)

        member = await _summary(sessions, project_id=None, allowed_project_ids={a, b}, days=1)
        assert member["searches"] == 8, "a member's projects only: no All-Projects row, no other project"
        async with sessions() as db:
            everything = (await db.execute(
                text("SELECT count(*) FROM search_query_events WHERE created_at >= :since"),
                {"since": datetime(2026, 10, 24, tzinfo=timezone.utc)},
            )).scalar_one()
        admin = await _summary(sessions, project_id=None, allowed_project_ids=None, days=1)
        assert admin["searches"] == everything >= 9, "ADMIN: every row, All-Projects searches included"

        empty = await _summary(sessions, project_id=fresh, allowed_project_ids=None, days=1)
        assert empty["measured"] is False and empty["searches"] == 0
        assert empty["zero_result_rate"] is None and empty["p95_latency_ms"] is None, "never 0 %"
    finally:
        await _cleanup(sessions, [a, b, fresh], created)


async def test_no_column_of_a_row_can_hold_the_query_text(env):
    from app.models.postgres import SearchQueryEvent
    from app.services.search_usage_service import query_hash

    sessions, _ = env
    (project,) = await _projects(sessions, 1)
    created = []
    try:
        created += await _events(sessions, [
            {"project_id": project, "q": f"  {TERM}  ", "entity_scope": "tests", "mode_requested": "hybrid",
             "mode_used": "keyword", "fallback_reason": "no_semantic_match"},
            {"project_id": None, "q": "status:failed checkout"},
        ])
        async with sessions() as db:
            rows = (await db.execute(
                select(SearchQueryEvent).where(SearchQueryEvent.id.in_(created)).order_by(SearchQueryEvent.q_length.desc())
            )).scalars().all()
            raw = (await db.execute(
                text("SELECT * FROM search_query_events WHERE id = ANY(:ids)"), {"ids": created},
            )).mappings().all()
            text_columns = (await db.execute(text(
                "SELECT column_name, data_type, character_maximum_length FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'search_query_events' "
                "AND data_type IN ('text', 'character varying', 'character', 'json', 'jsonb', 'bytea')"
            ))).all()

        assert rows[0].q_hash == query_hash(TERM) and rows[0].q_length == len(TERM)
        assert rows[1].q_hash == query_hash("status:failed checkout")
        fragments = {TERM.lower(), "zq-sentinel-7", "checkout", "timeout", "status:failed"}
        for row in raw:
            for column, value in row.items():
                rendered = str(value).lower()
                for fragment in fragments:
                    assert fragment not in rendered, (column, fragment)
        assert sorted((c, t, n) for c, t, n in text_columns) == [
            ("entity_scope", "character varying", 20),
            ("fallback_reason", "character varying", 30),
            ("mode_requested", "character varying", 10),
            ("mode_used", "character varying", 10),
            ("q_hash", "character varying", 64),
        ], "no column wide enough or free enough to hold a query"
    finally:
        await _cleanup(sessions, [project], created)


async def test_the_nightly_batch_deletes_only_rows_past_30_days(env):
    from app.services.search_usage_service import purge_expired_batch

    sessions, _ = env
    (project,) = await _projects(sessions, 1)
    created = []
    try:
        old = await _events(sessions, [
            {"project_id": project, "age": timedelta(days=31)},
            {"project_id": project, "age": timedelta(days=45)},
            {"project_id": None, "age": timedelta(days=60)},
        ])
        fresh = await _events(sessions, [
            {"project_id": project, "age": timedelta(days=29, hours=23)},
            {"project_id": None, "age": timedelta(hours=1)},
        ])
        created = old + fresh
        total = 0
        batches = 0
        while True:
            async with sessions() as db:
                deleted = await purge_expired_batch(db, now=NOW, batch=2)
                await db.commit()
            batches += 1
            total += deleted
            assert deleted <= 2
            if deleted == 0 or batches > 50:
                break
        async with sessions() as db:
            left = {r[0] for r in (await db.execute(
                text("SELECT id FROM search_query_events WHERE id = ANY(:ids)"), {"ids": created},
            )).all()}
        assert left == set(fresh), "only rows older than 30 days go, NULL-project rows included"
        assert total >= len(old)
    finally:
        await _cleanup(sessions, [project], created)


async def test_the_retention_purge_previews_and_deletes_the_projects_old_rows(env):
    from app.services import retention_service

    sessions, mongo = env
    project, other = await _projects(sessions, 2)
    created = []
    try:
        mine_old = await _events(sessions, [
            {"project_id": project, "age": timedelta(days=31)}, {"project_id": project, "age": timedelta(days=90)},
        ])
        mine_fresh = await _events(sessions, [{"project_id": project, "age": timedelta(days=2)}])
        others = await _events(sessions, [
            {"project_id": other, "age": timedelta(days=90)}, {"project_id": None, "age": timedelta(days=90)},
        ])
        created = mine_old + mine_fresh + others

        async with sessions() as db:
            preview = await retention_service.run_purge(
                db, project_id=project, mode="preview", now=NOW, mongo=mongo, storage=_NoStorage(),
            )
            await db.commit()
        assert preview["candidates"]["search_usage_rows"] == 2
        assert "search_usage_rows" not in preview["unmeasured"]

        async with sessions() as db:
            out = await retention_service.run_purge(
                db, project_id=project, mode="execute", now=NOW, mongo=mongo, storage=_NoStorage(),
            )
            await db.commit()
        assert out["counts"]["postgres"]["search_usage_rows"] == 2 == preview["candidates"]["search_usage_rows"]
        async with sessions() as db:
            left = {r[0] for r in (await db.execute(
                text("SELECT id FROM search_query_events WHERE id = ANY(:ids)"), {"ids": created},
            )).all()}
        assert left == set(mine_fresh + others), "that project's rows past 30 days only"
    finally:
        await _cleanup(sessions, [project, other], created)
