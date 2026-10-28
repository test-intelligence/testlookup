"""W22a ACT-04 — the keep-last floor, the purged-baseline stamp and the reference warnings, against a real
PostgreSQL (contract §2.2 F-2 … F-5, §6.3 G-C).

What a statement-routing fake cannot show:

1. **The floor's anti-joins.** 31 runs past the runs cutoff over two suites (``suite_names``), one with a
   SCALAR ``suite_names`` (an old row: the ``json_typeof`` guard must not raise "cannot extract elements from
   a scalar"): with N = 10 exactly the newest 10 + the project's newest PASSED run + the newest PASSED run of
   each suite survive (suite keys are lower-trimmed: " API " is "api"); with N = 0 only the PASSED arms.
   ``floor`` in the preview matches.
2. **The stamp.** ``run_baselines`` rows pointing at purged runs get ``baseline_purged_at`` (the FIRST date
   sticks) and keep ``baseline_build_number``; the SET NULL clears ``baseline_run_id``.
3. **The reference count.** ``references_broken`` lists ``run_baselines.baseline_run_id`` with the SURVIVING
   rows only — a baseline row whose own run is purged cascades away and is not counted (D-18) — and the
   scheduled job's ``references_broken`` matches the preview.

Mongo / storage are in-memory doubles (patched in for the sweep's un-injected ``run_purge``, with the cache /
search-index entry points stubbed — no real Mongo / Chroma / MinIO client is bound). The fixture awaits
``dispose_engine_for_loop()`` before building its engine and after disposing it, and closes ONLY Redis (the
sweep bumps the analytics epoch). Every timestamp comes from a ``now`` built in the test; every row lives under
a fresh project removed at the end. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (= ``DATABASE_URL``) at head
(0240). To be listed in the ``postgres-integration`` job of ``.github/workflows/ci.yml`` (contract §6.4).
"""
from __future__ import annotations

import json
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


class _NoMongo:
    def __getitem__(self, name):
        return self

    async def count_documents(self, _filt):
        return 0

    async def delete_many(self, _filt):
        return type("R", (), {"deleted_count": 0})()


class _NoStorage:
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

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        await close_redis()


@pytest.fixture
def no_stores(monkeypatch):
    from app.services import analysis_cache_retention, retention_service, semantic_search

    async def _caches(project_id, *, cutoff, execute):
        return {"redis": None, "semantic": None}

    async def _search(project_id, *, execute):
        return None

    monkeypatch.setattr(retention_service, "get_mongo_db", lambda: _NoMongo())
    monkeypatch.setattr(retention_service, "get_storage_provider", lambda: _NoStorage())
    monkeypatch.setattr(analysis_cache_retention, "purge_project_analysis_caches", _caches)
    monkeypatch.setattr(semantic_search, "purge_project_documents", _search)


#: index -> (status, suite_names, primary_suite_name); every other index is a FAILED "api" run. Index 0 is the
#: newest; run i is (100 + i) days old — all 31 are past the 30-day runs cutoff.
_SPECIAL = {
    12: ("PASSED", "legacy", None),      # the project's newest PASSED run; SCALAR suite_names, no suite at all
    15: ("PASSED", ["api"], "api"),      # newest PASSED of "api"
    20: ("PASSED", ["ui"], "ui"),        # newest PASSED of "ui"
    25: ("PASSED", [" API "], "api"),    # an older PASSED "api" run (lower-trimmed key) — not kept
    27: ("PASSED", ["ui"], "ui"),        # an older PASSED "ui" run — not kept
}
_PASSED_KEEPS = {12, 15, 20}


async def _seed(sessions, now, *, keep_last_runs: int) -> tuple[uuid.UUID, list]:
    from app.models.postgres import Project, ProjectRetentionPolicy, TestRun

    pid = uuid.uuid4()
    runs = []
    async with sessions() as db:
        db.add(Project(id=pid, name=f"gc-floor-{pid.hex[:10]}", slug=f"gc-floor-{pid.hex[:10]}", is_active=True))
        await db.flush()
        db.add(ProjectRetentionPolicy(project_id=pid, enabled=True, raw_events_days=7, runs_days=30,
                                      artifacts_days=7, audit_days=365, keep_last_runs=keep_last_runs))
        for i in range(31):
            status, suites, primary = _SPECIAL.get(i, ("FAILED", ["api"], "api"))
            run = TestRun(id=uuid.uuid4(), project_id=pid, build_number=f"gc-floor-{i:02d}", jenkins_job="gc",
                          status=status, ingestion_source="upload", branch="main", suite_names=suites,
                          primary_suite_name=primary)
            run.created_at = now - timedelta(days=100 + i)
            db.add(run)
            runs.append(run)
        await db.commit()
    return pid, runs


async def _left(sessions, runs) -> set[int]:
    async with sessions() as db:
        rows = (await db.execute(text("SELECT id FROM test_runs WHERE id = ANY(:i)"),
                                 {"i": [r.id for r in runs]})).all()
    present = {r[0] for r in rows}
    return {i for i, run in enumerate(runs) if run.id in present}


async def _drop(sessions, pid) -> None:
    async with sessions() as db:
        await db.execute(text("DELETE FROM settings_audit_log WHERE setting_key = :k"), {"k": f"retention_purge:{pid}"})
        await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": pid})
        await db.commit()


@pytest.mark.parametrize("keep_last_runs", [10, 0])
async def test_the_floor_keeps_the_newest_n_and_the_newest_passed_of_the_project_and_each_suite(env, keep_last_runs):
    from app.services import retention_service

    sessions = env
    now = datetime.now(timezone.utc)
    pid, runs = await _seed(sessions, now, keep_last_runs=keep_last_runs)
    expected = set(range(keep_last_runs)) | _PASSED_KEEPS
    try:
        async with sessions() as db:
            preview = await retention_service.run_purge(db, project_id=pid, mode="preview", now=now,
                                                        mongo=_NoMongo(), storage=_NoStorage())
        assert preview["candidates"]["runs"] == 31 - len(expected)
        assert preview["floor"] == {
            "keep_last_runs": keep_last_runs, "runs_kept": len(expected),
            "latest_passed_kept": True, "suites_kept": 2,
        }
        async with sessions() as db:
            out = await retention_service.run_purge(db, project_id=pid, mode="execute", now=now,
                                                    mongo=_NoMongo(), storage=_NoStorage())
            await db.commit()
        assert await _left(sessions, runs) == expected, (
            "exactly the newest N + the project's newest PASSED + each suite's newest PASSED survive"
        )
        assert out["counts"]["postgres"]["runs"] == 31 - len(expected)
        assert out["floor"]["runs_kept"] == len(expected)
    finally:
        await _drop(sessions, pid)


async def test_purged_baselines_are_stamped_and_only_surviving_references_are_counted(env, no_stores):
    from app.models.postgres import RunBaseline
    from app.services import retention_service
    from app.worker.tasks import _retention_purge_sweep

    sessions = env
    now = datetime.now(timezone.utc)
    pid, runs = await _seed(sessions, now, keep_last_runs=10)   # survivors: 0-9, 12, 15, 20
    earlier = now - timedelta(days=5)
    rows = {
        "counted": RunBaseline(id=uuid.uuid4(), run_id=runs[0].id, baseline_run_id=runs[11].id,
                               baseline_build_number="gc-floor-11", selection_reason="newest_passed",
                               classification="same"),
        "first_date": RunBaseline(id=uuid.uuid4(), run_id=runs[2].id, baseline_run_id=runs[16].id,
                                  baseline_build_number="gc-floor-16", selection_reason="newest_passed",
                                  classification="same", baseline_purged_at=earlier),
        "cascaded": RunBaseline(id=uuid.uuid4(), run_id=runs[13].id, baseline_run_id=runs[14].id,
                                baseline_build_number="gc-floor-14", selection_reason="newest_passed",
                                classification="same"),
        "kept": RunBaseline(id=uuid.uuid4(), run_id=runs[1].id, baseline_run_id=runs[12].id,
                            baseline_build_number="gc-floor-12", selection_reason="newest_passed",
                            classification="same"),
    }
    try:
        async with sessions() as db:
            for row in rows.values():
                db.add(row)
            await db.commit()
        async with sessions() as db:
            preview = await retention_service.run_purge(db, project_id=pid, mode="preview", now=now,
                                                        mongo=_NoMongo(), storage=_NoStorage())
        assert preview["references_measured"] is True
        assert preview["references_broken"] == [{
            "table": "run_baselines", "column": "baseline_run_id", "count": 2,
            "label": "2 runs lose their baseline link",
        }], "the row whose OWN run is purged cascades away and is not counted"

        summary = await _retention_purge_sweep(str(pid))
        assert summary["errors"] == 0, summary
        async with sessions() as db:
            job = (await db.execute(text(
                "SELECT references_broken FROM deletion_jobs WHERE project_id = :p AND job_kind = 'scheduled' "
                "ORDER BY requested_at DESC LIMIT 1"), {"p": pid})).scalar()
            stamped = {r[0]: (r[1], r[2], r[3]) for r in (await db.execute(text(
                "SELECT id, baseline_run_id, baseline_build_number, baseline_purged_at FROM run_baselines "
                "WHERE id = ANY(:i)"), {"i": [r.id for r in rows.values()]})).all()}
        job = json.loads(job) if isinstance(job, str) else job
        assert job == {"measured": True, "total": 2, "run_baselines.baseline_run_id": 2}, "the job matches the preview"

        link, build, purged_at = stamped[rows["counted"].id]
        assert link is None and build == "gc-floor-11" and purged_at is not None, (
            "stamped before the delete; the SET NULL clears the link; the build number stays"
        )
        assert stamped[rows["first_date"].id][2] == earlier, "the FIRST purge date sticks"
        assert rows["cascaded"].id not in stamped, "its own run was purged: the row cascaded away"
        assert stamped[rows["kept"].id] == (runs[12].id, "gc-floor-12", None), "a surviving baseline is untouched"
    finally:
        await _drop(sessions, pid)
