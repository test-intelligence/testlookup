"""The retention purge's search-index scope, against a real PostgreSQL (owner decision 2026-11-03).

The nightly purge used to call ``semantic_search.purge_project_documents`` for every project it visited, so a
retention-enabled ACTIVE project lost its WHOLE test-case search index every night (search stayed empty until
a full reindex), whatever the keep-last floor or a hold kept. The owner: "not needed this way". Now

* an active project's purge removes the documents of exactly the runs it deletes
  (``purge_runs_documents(project, cand.purge_run_strs)``) and never calls the project-wide delete;
* a deleted project (``is_active = false``) still retires its whole index (SEARCH-009);
* a run the floor keeps is not in that list, so its documents stay.

``run_purge`` is called WITHOUT injected stores (the only path that visits the derived stores), with
``get_mongo_db`` / ``get_storage_provider`` patched to in-memory doubles and the two search entry points to
recorders, so no Mongo, Chroma or MinIO client is bound. Every row lives under a fresh project removed at the
end. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head; listed in the ``postgres-integration`` job of
``.github/workflows/ci.yml``.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _MemMongo:
    """Every collection empty; only ``count_documents`` / ``delete_many`` (what the purge uses)."""

    class _Collection:
        async def count_documents(self, filt):
            return 0

        async def delete_many(self, filt):
            return type("R", (), {"deleted_count": 0})()

    def __getitem__(self, name):
        return self._Collection()


class _MemStorage:
    async def list_objects(self, prefix, bucket=None):
        return []

    async def delete_prefix(self, prefix, bucket=None):
        return 0

    async def delete_object(self, key, bucket=None):
        return None


@pytest_asyncio.fixture(loop_scope="module")
async def env():
    from app.db import postgres as app_postgres
    from app.db.redis_client import close_redis

    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        await close_redis()


@pytest.fixture
def search_calls(monkeypatch):
    """Records which search-index purge ran, with what; the other derived stores stubbed."""
    from app.services import agent_memory_service, analysis_cache_retention, retention_service, semantic_search

    calls: list[tuple] = []

    async def _caches(project_id, *, cutoff, execute):
        return {"redis": None, "semantic": None}

    async def _project_wide(project_id, *, execute):
        calls.append(("project", project_id, execute))
        return 7

    async def _runs(project_id, run_ids, *, execute):
        calls.append(("runs", project_id, sorted(run_ids), execute))
        return len(run_ids)

    async def _vectors(project_id, ids):
        return len(ids)

    monkeypatch.setattr(retention_service, "get_mongo_db", lambda: _MemMongo())
    monkeypatch.setattr(retention_service, "get_storage_provider", lambda: _MemStorage())
    monkeypatch.setattr(analysis_cache_retention, "purge_project_analysis_caches", _caches)
    monkeypatch.setattr(semantic_search, "purge_project_documents", _project_wide)
    monkeypatch.setattr(semantic_search, "purge_runs_documents", _runs)
    monkeypatch.setattr(agent_memory_service, "purge_memory_vectors", _vectors)
    return calls


async def _project(sessions, *, active: bool, keep_last_runs: int = 0) -> uuid.UUID:
    from app.models.postgres import Project, ProjectRetentionPolicy

    pid = uuid.uuid4()
    async with sessions() as db:
        db.add(Project(id=pid, name=f"gc-search-{pid.hex[:10]}", slug=f"gc-search-{pid.hex[:10]}", is_active=active))
        await db.flush()
        db.add(ProjectRetentionPolicy(project_id=pid, enabled=True, raw_events_days=7, runs_days=30,
                                      artifacts_days=7, audit_days=365, keep_last_runs=keep_last_runs))
        await db.commit()
    return pid


async def _runs(sessions, pid, now, *ages_days: int) -> list[uuid.UUID]:
    from app.models.postgres import TestRun

    ids = []
    async with sessions() as db:
        for days in ages_days:
            run = TestRun(id=uuid.uuid4(), project_id=pid, build_number=f"gc-{uuid.uuid4().hex[:8]}",
                          jenkins_job="gc", status="FAILED", ingestion_source="upload", branch="main")
            run.created_at = now - timedelta(days=days)
            db.add(run)
            await db.flush()
            ids.append(run.id)
        await db.commit()
    return ids


async def _purge(sessions, pid, now, *, mode: str) -> dict:
    from app.services import retention_service

    async with sessions() as db:   # NOT injected: the derived-store entry points (recorded) are visited
        out = await retention_service.run_purge(db, project_id=pid, mode=mode, now=now)
        await db.commit()
    return out


async def _cleanup(sessions, pid) -> None:
    async with sessions() as db:
        await db.execute(text("DELETE FROM settings_audit_log WHERE setting_key = :k"),
                         {"k": f"retention_purge:{pid}"})
        await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
        await db.commit()


def _search_count(out: dict) -> int | None:
    """The preview's ``candidates.search_index_documents``; the execute's ``counts.search_index.documents_deleted``."""
    if out["mode"] == "preview":
        return out["candidates"]["search_index_documents"]
    return out["counts"]["search_index"]["documents_deleted"]


async def test_an_active_projects_purge_removes_only_the_purged_runs_documents(env, search_calls):
    """The owner's case: two runs past the 30-day window and one inside it. Only the two aged runs' documents
    go — never the project-wide delete — in the preview (counted) and in the execute (deleted)."""
    sessions = env
    now = datetime.now(timezone.utc)
    pid = await _project(sessions, active=True)
    try:
        aged_1, aged_2, fresh = await _runs(sessions, pid, now, 400, 200, 3)
        expected = sorted([str(aged_1), str(aged_2)])

        preview = await _purge(sessions, pid, now, mode="preview")
        assert search_calls == [("runs", str(pid), expected, False)]
        assert _search_count(preview) == 2

        search_calls.clear()
        out = await _purge(sessions, pid, now, mode="execute")
        assert search_calls == [("runs", str(pid), expected, True)]
        assert not any(call[0] == "project" for call in search_calls), "an active project keeps its index"
        assert str(fresh) not in expected
        assert _search_count(out) == 2
    finally:
        await _cleanup(sessions, pid)


async def test_a_run_the_floor_keeps_keeps_its_documents(env, search_calls):
    """``keep_last_runs = 1``: the newest aged run is kept by the floor, so it is not in the list the search
    purge receives (the floor anti-join is in ``purge_run_ids``; the project-wide delete ignored it)."""
    sessions = env
    now = datetime.now(timezone.utc)
    pid = await _project(sessions, active=True, keep_last_runs=1)
    try:
        oldest, newest_aged = await _runs(sessions, pid, now, 400, 200)
        await _purge(sessions, pid, now, mode="execute")
        assert search_calls == [("runs", str(pid), [str(oldest)], True)]
        assert str(newest_aged) not in search_calls[0][2]
    finally:
        await _cleanup(sessions, pid)


async def test_an_active_project_with_nothing_to_purge_deletes_no_document(env, search_calls):
    """Every run inside the window: the search purge receives an empty list (0, the store not visited by the
    real function), not the project-wide delete."""
    sessions = env
    now = datetime.now(timezone.utc)
    pid = await _project(sessions, active=True)
    try:
        await _runs(sessions, pid, now, 2, 5)
        await _purge(sessions, pid, now, mode="execute")
        assert search_calls == [("runs", str(pid), [], True)]
    finally:
        await _cleanup(sessions, pid)


async def test_a_deleted_project_still_retires_its_whole_index(env, search_calls):
    """SEARCH-009 unchanged: a soft-deleted project's purge removes every document it owns (the indexers stopped
    writing for it; nothing else ever retires them)."""
    sessions = env
    now = datetime.now(timezone.utc)
    pid = await _project(sessions, active=False)
    try:
        await _runs(sessions, pid, now, 400, 3)
        out = await _purge(sessions, pid, now, mode="execute")
        assert search_calls == [("project", str(pid), True)]
        assert _search_count(out) == 7
    finally:
        await _cleanup(sessions, pid)
