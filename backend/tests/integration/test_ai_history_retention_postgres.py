"""AIH-06 retention of AI history, against a real PostgreSQL and a real MongoDB.

What a fake session cannot show:

1. **The over-cap query is right.** ``plan_over_cap`` finds the test results
   holding more than the cap with a GROUP BY / HAVING subquery; only a real
   database runs it. A cap of 3 over five versions keeps v1, the version a
   published decision report cites, and the newest -- in BOTH stores.
2. **Preview == execute.** The preview's ``ai_history_versions`` equals what
   the execute removed, measured in the databases afterwards (rows and documents
   actually gone), not from the returned counts alone.
3. **The cascade count is the cascade.** ``ai_analysis_versions`` rows of a
   purged run are counted before the run delete and are gone after it (FK
   ``ON DELETE CASCADE``); the run's ``ai_run_versions`` documents go on the
   runs clock.
4. **Fail closed.** With the decision reports unreadable, nothing is pruned in
   either store.

Every row is written under a fresh project and removed at the end (the
databases are shared). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to
head (0206) and a reachable ``MONGO_URI``. To be listed in the
``postgres-integration`` job of ``.github/workflows/ci.yml``.
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

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _RecordingStorage:
    async def list_objects(self, prefix, *, bucket=None):
        return []

    async def delete_prefix(self, prefix, *, bucket=None):
        return 0

    async def delete_object(self, key, *, bucket=None):
        return None


class _ReportsDown:
    """A decision_reports collection that cannot be read."""

    def find(self, *_args, **_kwargs):
        raise RuntimeError("decision_reports unreachable")

    async def count_documents(self, *_args, **_kwargs):
        return 0

    async def delete_many(self, *_args, **_kwargs):
        return type("R", (), {"deleted_count": 0})()


class _CitationsDown:
    """The real Mongo handle, except that the decision reports cannot be read."""

    def __init__(self, mongo):
        self._mongo = mongo

    def __getitem__(self, name):
        from app.db.mongo import Collections

        return _ReportsDown() if name == Collections.DECISION_REPORTS else self._mongo[name]


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


async def _seed(sessions, mongo, *, caps=(3, 3), cited=(2,)):
    """One project: a run past the runs clock (2 per-test versions, 2 run
    versions) and a kept run with 5 versions of its digest and of one test
    result, pipelines P1..P5; ``cited`` = the pipelines a published report cites."""
    from app.db.mongo import Collections
    from app.models.postgres import (
        AgentPipelineRun,
        AIAnalysisVersion,
        Project,
        ProjectRetentionPolicy,
        TestCase,
        TestRun,
    )

    tag = uuid.uuid4().hex[:10]
    w = {"project": uuid.uuid4(), "keep": uuid.uuid4(), "old": uuid.uuid4(),
         "keep_tc": uuid.uuid4(), "old_tc": uuid.uuid4(), "pipes": [uuid.uuid4() for _ in range(5)]}
    async with sessions() as db:
        db.add(Project(id=w["project"], name=f"aihr-{tag}", slug=f"aihr-{tag}", is_active=True))
        await db.flush()
        # W22a: keep_last_runs = 0 — the keep-last floor (default 10) would
        # otherwise keep the old run these counts assume purged.
        db.add(ProjectRetentionPolicy(
            project_id=w["project"], enabled=True, raw_events_days=7, runs_days=30, artifacts_days=7,
            audit_days=365, ai_versions_per_run=caps[0], ai_versions_per_test=caps[1], keep_last_runs=0,
        ))
        for run_id, age, label in ((w["keep"], timedelta(hours=1), "keep"), (w["old"], timedelta(days=4000), "old")):
            run = TestRun(id=run_id, project_id=w["project"], build_number=f"aihr-{tag}-{label}", jenkins_job="aihr",
                          status="FAILED", ingestion_source="upload", branch="main", primary_suite_name="checkout")
            run.created_at = NOW - age
            db.add(run)
        await db.flush()
        for tc_id, run_id in ((w["keep_tc"], w["keep"]), (w["old_tc"], w["old"])):
            db.add(TestCase(id=tc_id, test_run_id=run_id, test_name="test_pay", class_name="checkout",
                            suite_name="checkout", test_fingerprint=f"fp-{tag}", status="FAILED",
                            duration_ms=10))
        for pid in w["pipes"]:
            db.add(AgentPipelineRun(id=pid, test_run_id=w["keep"], workflow_type="offline", status="completed",
                                    completed_at=NOW))
        await db.flush()

        def _version(tc, run, version, pipeline):
            return AIAnalysisVersion(
                test_case_id=tc, test_run_id=run, project_id=w["project"], test_fingerprint=f"fp-{tag}",
                version=version, trigger="pipeline" if pipeline else "backfill", pipeline_run_id=pipeline,
                content_sha256=f"{version:064x}", failure_category="PRODUCT_BUG", confidence_score=80,
            )

        for v in range(1, 6):
            db.add(_version(w["keep_tc"], w["keep"], v, w["pipes"][v - 1]))
        db.add(_version(w["old_tc"], w["old"], 1, None))
        db.add(_version(w["old_tc"], w["old"], 2, None))
        await db.commit()

    def _doc(run, kind, version, pipeline):
        return {"version_id": str(uuid.uuid4()), "test_run_id": str(run), "project_id": str(w["project"]),
                "kind": kind, "version": version, "pipeline_run_id": str(pipeline) if pipeline else None,
                "trigger": "pipeline" if pipeline else "backfill", "content_sha256": f"{version:064x}",
                "generated_at": NOW, "created_at": NOW, "schema_version": 1, "provenance": {}, "meta": {},
                "payload": {}}

    await mongo[Collections.AI_RUN_VERSIONS].insert_many([
        *[_doc(w["keep"], "analysis_digest", v, w["pipes"][v - 1]) for v in range(1, 6)],
        _doc(w["keep"], "run_summary", 1, w["pipes"][0]),
        _doc(w["old"], "analysis_digest", 1, None),
        _doc(w["old"], "run_summary", 1, None),
    ])
    if cited:
        await mongo[Collections.DECISION_REPORTS].insert_many([
            {"report_id": str(uuid.uuid4()), "project_id": str(w["project"]), "test_run_id": str(w["keep"]),
             "pipeline_run_id": str(w["pipes"][v - 1]), "report_version": i + 1, "status": "published",
             "generated_at": NOW}
            for i, v in enumerate(cited)
        ])
    return w


async def _cleanup(sessions, mongo, w):
    from app.db.mongo import Collections

    async with sessions() as db:
        await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": w["project"]})
        await db.commit()
    for name in (Collections.AI_RUN_VERSIONS, Collections.DECISION_REPORTS):
        await mongo[name].delete_many({"project_id": str(w["project"])})


async def _test_versions(sessions, tc) -> list[int]:
    async with sessions() as db:
        rows = await db.execute(
            text("SELECT version FROM ai_analysis_versions WHERE test_case_id = :t ORDER BY version"), {"t": tc},
        )
        return [r[0] for r in rows]


async def _total(sessions, mongo, w) -> int:
    """Every AI version of the project, in both stores."""
    from app.db.mongo import Collections

    async with sessions() as db:
        rows = (await db.execute(
            text("SELECT count(*) FROM ai_analysis_versions WHERE project_id = :p"), {"p": w["project"]},
        )).scalar_one()
    docs = await mongo[Collections.AI_RUN_VERSIONS].count_documents({"project_id": str(w["project"])})
    return int(rows) + int(docs)


async def _doc_versions(mongo, run, kind="analysis_digest") -> list[int]:
    from app.db.mongo import Collections

    docs = await mongo[Collections.AI_RUN_VERSIONS].find(
        {"test_run_id": str(run), "kind": kind}, {"_id": 0, "version": 1},
    ).to_list(length=None)
    return sorted(d["version"] for d in docs)


async def test_preview_equals_execute_and_v1_and_the_cited_version_survive(env):
    from app.services import retention_service

    sessions, mongo = env
    w = await _seed(sessions, mongo, caps=(3, 3), cited=(2,))
    try:
        async with sessions() as db:
            preview = await retention_service.run_purge(
                db, project_id=w["project"], mode="preview", mongo=mongo, storage=_RecordingStorage(),
            )
            await db.commit()
        # 2 run docs of the purged run + 2 cascaded test versions + 2 test versions + 2 digests over the cap.
        assert preview["candidates"]["ai_history_versions"] == 8
        assert await _test_versions(sessions, w["keep_tc"]) == [1, 2, 3, 4, 5], "preview wrote nothing"
        before = await _total(sessions, mongo, w)
        assert before == 15

        async with sessions() as db:
            out = await retention_service.run_purge(
                db, project_id=w["project"], mode="execute", mongo=mongo, storage=_RecordingStorage(),
            )
            await db.commit()

        assert await _test_versions(sessions, w["keep_tc"]) == [1, 2, 5], "v1 + the cited v2 + the newest"
        assert await _doc_versions(mongo, w["keep"]) == [1, 2, 5]
        assert await _doc_versions(mongo, w["keep"], "run_summary") == [1], "under the cap: untouched"
        assert await _test_versions(sessions, w["old_tc"]) == [], "cascaded with the purged run"
        assert await _doc_versions(mongo, w["old"]) == [] and await _doc_versions(mongo, w["old"], "run_summary") == []

        assert out["counts"]["ai_history"] == {
            "analysis_versions_cascaded": 2, "analysis_versions_over_cap": 2, "run_versions_over_cap": 2,
        }
        assert out["counts"]["mongo"]["ai_run_versions"] == 2
        # Measured in the databases: 7 rows + 8 documents before, 3 + 4 after.
        assert before - await _total(sessions, mongo, w) == preview["candidates"]["ai_history_versions"]
    finally:
        await _cleanup(sessions, mongo, w)


async def test_an_unreadable_citation_store_prunes_nothing(env):
    from app.services import retention_service

    sessions, mongo = env
    w = await _seed(sessions, mongo, caps=(3, 3), cited=())
    try:
        down = _CitationsDown(mongo)
        async with sessions() as db:
            preview = await retention_service.run_purge(
                db, project_id=w["project"], mode="preview", mongo=down, storage=_RecordingStorage(),
            )
            out = await retention_service.run_purge(
                db, project_id=w["project"], mode="execute", mongo=down, storage=_RecordingStorage(),
            )
            await db.commit()
        assert preview["candidates"]["ai_history_versions"] == 4, "the runs clock only"
        assert out["counts"]["ai_history"]["analysis_versions_over_cap"] == 0
        assert out["counts"]["ai_history"]["run_versions_over_cap"] == 0
        assert await _test_versions(sessions, w["keep_tc"]) == [1, 2, 3, 4, 5]
        assert await _doc_versions(mongo, w["keep"]) == [1, 2, 3, 4, 5]
    finally:
        await _cleanup(sessions, mongo, w)


async def test_a_cap_of_3_without_citations_keeps_v1_and_the_two_newest(env):
    from app.services import ai_history_retention as air
    from app.services.ai_history_core import AIHistoryCaps

    sessions, mongo = env
    w = await _seed(sessions, mongo, caps=(3, 3), cited=())
    try:
        async with sessions() as db:
            plan = await air.plan_over_cap(
                db, mongo, project_id=w["project"], caps=AIHistoryCaps(3, 3), exclude_run_ids=[w["old"]],
            )
            assert not plan.unreadable
            await air.execute_over_cap(db, mongo, plan)
            await db.commit()
        assert await _test_versions(sessions, w["keep_tc"]) == [1, 4, 5]
        assert await _doc_versions(mongo, w["keep"]) == [1, 4, 5]
        assert await _test_versions(sessions, w["old_tc"]) == [1, 2], "an excluded run is left to its clock"
    finally:
        await _cleanup(sessions, mongo, w)


async def test_the_cascade_count_is_what_the_run_delete_removes(env):
    from app.services import ai_history_retention as air

    sessions, mongo = env
    w = await _seed(sessions, mongo, cited=())
    try:
        async with sessions() as db:
            counted = await air.count_cascaded(db, [w["old"], w["keep"]])
            await db.execute(text("DELETE FROM test_runs WHERE id = ANY(:ids)"), {"ids": [w["old"], w["keep"]]})
            await db.commit()
        assert counted == 7
        async with sessions() as db:
            left = (await db.execute(
                text("SELECT count(*) FROM ai_analysis_versions WHERE project_id = :p"), {"p": w["project"]},
            )).scalar_one()
        assert left == 0
    finally:
        await _cleanup(sessions, mongo, w)
