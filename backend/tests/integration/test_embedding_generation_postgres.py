"""W12 SRCH-02 §2.4 test 6: the generation build's keyset job and the state lock on real PostgreSQL.

What a fake session cannot show:

1. **Migration 0226's columns** exist and are nullable on ``semantic_reindex_jobs``.
2. **``build_generation_index``** claims ONE job row under ``scope_key =
   "generation:<tag>"`` with ``collection`` / ``embedder_key`` set, commits a
   checkpoint per page, hands the lease back at the deadline, and the next
   call RESUMES from the committed cursor (no row upserted twice, every row
   upserted once) until the job succeeds. Its statements per page are counted.
3. **Two concurrent claims** of the same generation: one builds, one is
   ``GenerationIndexBusy`` (the row lock / unique key decides).
4. **``lock_state``** is ``SELECT … FOR UPDATE``: a second writer waits for the
   first to commit and then reads ITS state (two settings PUTs serialise).

ChromaDB is an in-memory recording fake (``semantic_search._get_chroma_client``
patched). Every row is written under a fresh project and removed at the end;
the ``embedding_index`` state row is restored to what it was. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (0227).
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PAGE = 3
N_CASES = 7


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _Collection:
    def __init__(self, name):
        self.name = name
        self.upserts: list[list[str]] = []

    def upsert(self, ids, documents=None, metadatas=None, embeddings=None):
        self.upserts.append(list(ids))

    @property
    def ids(self) -> list[str]:
        return [i for batch in self.upserts for i in batch]


class _Client:
    def __init__(self):
        self.collections: dict[str, _Collection] = {}
        self.kwargs: list[dict] = []

    def get_or_create_collection(self, name, **kwargs):
        self.kwargs.append(kwargs)
        return self.collections.setdefault(name, _Collection(name))


@pytest_asyncio.fixture(loop_scope="module")
async def env():
    from app.db import postgres as app_postgres

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine, sessions
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def world(env, monkeypatch):
    """A fresh project with N_CASES results, a fresh generation, a recording Chroma client."""
    from app.services import chroma_collections as cc
    from app.services import semantic_search
    from app.services.embedding_function import DEFAULT_EMBEDDER
    from tests.integration.ai_history_seed import seed_project, seed_run

    engine, sessions = env
    async with sessions() as db:
        project_id = await seed_project(db, "gc5")
        _run_id, case_ids = await seed_run(db, project_id, N_CASES)
        await db.commit()
    generation = cc.Generation("t" + uuid.uuid4().hex[:7], DEFAULT_EMBEDDER)
    client = _Client()

    async def chroma():
        return client

    monkeypatch.setattr(semantic_search, "_get_chroma_client", chroma)
    monkeypatch.setattr(semantic_search.settings, "SEARCH_INDEX_BATCH_SIZE", PAGE)
    scope_key = semantic_search.generation_scope_key(generation)
    try:
        yield {"sessions": sessions, "engine": engine, "generation": generation, "client": client,
               "case_ids": {str(c) for c in case_ids}, "scope_key": scope_key, "project_id": project_id}
    finally:
        async with sessions() as db:
            await db.execute(text("DELETE FROM semantic_reindex_jobs WHERE scope_key = :k"), {"k": scope_key})
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": project_id})
            await db.commit()


async def _job(sessions, scope_key):
    from app.models.postgres import SemanticReindexJob

    async with sessions() as db:
        result = await db.execute(select(SemanticReindexJob).where(SemanticReindexJob.scope_key == scope_key))
        return result.scalar_one_or_none()


async def test_migration_0226_columns_exist_and_are_nullable(env):
    _engine, sessions = env
    async with sessions() as db:
        rows = (await db.execute(text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'semantic_reindex_jobs' AND column_name IN ('collection', 'embedder_key')"
        ))).all()
    assert sorted(rows) == [("collection", "YES"), ("embedder_key", "YES")]


async def test_the_build_claims_its_own_job_checkpoints_and_resumes_after_the_deadline(world):
    from app.services import semantic_search

    sessions, generation, client = world["sessions"], world["generation"], world["client"]
    statements: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.split(None, 1)[0].upper())

    # Slice 1: the deadline has already passed -> one page, then the lease is handed back.
    async with sessions() as db:
        total = await semantic_search.count_indexable_test_cases(db)
        assert total >= N_CASES
        processed, finished = await semantic_search.build_generation_index(db, generation, deadline=time.monotonic())
    assert (processed, finished) == (PAGE, False)
    job = await _job(sessions, world["scope_key"])
    assert job is not None and job.status == "running" and job.processed_count == PAGE
    assert job.collection == f"{generation.prefix}_test_case_search" and job.embedder_key == "default"
    assert job.cursor_id is not None and job.project_id is None
    assert job.lease_expires_at is not None and job.lease_expires_at.timestamp() <= time.time() + 1
    collection = client.collections[job.collection]
    assert client.kwargs == [{}]                         # the bundled embedder: no embedding_function kwarg
    first = list(collection.ids)
    assert len(first) == PAGE

    # Slice 2 resumes from the committed cursor and runs to the end.
    event.listen(world["engine"].sync_engine, "before_cursor_execute", count)
    try:
        async with sessions() as db:
            processed, finished = await semantic_search.build_generation_index(db, generation, deadline=time.monotonic() + 120)
    finally:
        event.remove(world["engine"].sync_engine, "before_cursor_execute", count)
    assert finished is True and processed == total
    assert len(collection.ids) == len(set(collection.ids)) == total     # every row exactly once
    assert world["case_ids"] <= set(collection.ids)
    job = await _job(sessions, world["scope_key"])
    assert job.status == "succeeded" and job.processed_count == total
    async with sessions() as db:
        assert await semantic_search.generation_high_water(db, generation) == str(job.high_water_id)

    pages = len(collection.upserts) - 1                 # slice 2's pages
    # The resume: claim (SELECT … FOR UPDATE, UPDATE lease + fence); THREE statements
    # per page (lease UPDATE, page SELECT, checkpoint UPDATE), the empty last page
    # turning the job ``finalizing``, then ``succeeded`` -- each UPDATE its own commit.
    assert statements == (
        ["SELECT", "UPDATE"] + ["UPDATE", "SELECT", "UPDATE"] * (pages + 1) + ["UPDATE"]
    ), statements


async def test_two_concurrent_claims_build_once_and_answer_busy_once(world):
    from app.services import semantic_search

    sessions, generation = world["sessions"], world["generation"]

    async def build():
        async with sessions() as db:
            return await semantic_search.build_generation_index(db, generation, deadline=time.monotonic() + 120)

    outcomes = await asyncio.gather(build(), build(), return_exceptions=True)

    busy = [o for o in outcomes if isinstance(o, semantic_search.GenerationIndexBusy)]
    built = [o for o in outcomes if isinstance(o, tuple)]
    assert len(busy) == 1 and len(built) == 1, outcomes
    assert built[0][1] is True
    assert world["case_ids"] <= set(world["client"].collections[f"{generation.prefix}_test_case_search"].ids)


async def test_lock_state_serialises_two_writers(env):
    from datetime import datetime, timezone

    from app.services import chroma_collections as cc
    from app.services import embedding_index_state as eis
    from app.services.embedding_function import DEFAULT_EMBEDDER

    _engine, sessions = env
    async with sessions() as db:
        before = (await db.execute(
            text("SELECT value FROM app_settings WHERE key = :k"), {"k": eis.EMBEDDING_STATE_KEY}
        )).scalar_one_or_none()
    wanted = cc.Generation("t" + uuid.uuid4().hex[:7], DEFAULT_EMBEDDER)
    try:
        async with sessions() as first, sessions() as second:
            row, state = await eis.lock_state(first)            # writer 1 holds the row
            fence = state.fence
            await second.execute(text("SET LOCAL lock_timeout = '300ms'"))
            with pytest.raises(DBAPIError) as waited:
                await eis.lock_state(second)                     # writer 2 must wait for it
            assert "lock" in str(waited.value.orig).lower()
            await second.rollback()

            after = eis.request_target(state, wanted, actor_id=None, now=datetime.now(timezone.utc))
            eis.stage_state(first, row, after, actor_id=None)
            await first.commit()

            _row, seen = await eis.lock_state(second)            # now it proceeds, and reads writer 1's state
            assert seen.target_generation() == wanted and seen.fence == fence + 1
            await second.rollback()
    finally:
        async with sessions() as db:
            if before is None:
                await db.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": eis.EMBEDDING_STATE_KEY})
            else:
                from app.models.postgres import AppSetting

                stored = (await db.execute(
                    select(AppSetting).where(AppSetting.key == eis.EMBEDDING_STATE_KEY)
                )).scalar_one()
                stored.value = before
            await db.commit()
