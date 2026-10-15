"""AIH-01/06 on real MongoDB (+ the real PostgreSQL subject lock): ``ai_run_versions``.

* ``ensure_indexes()`` creates the four ``ai_run_versions`` indexes, and the
  ``run_summaries`` unique index on ``test_run_id`` is unchanged.
* Five concurrent ``record_run_version`` calls for one run+kind get versions
  1..5, no duplicate; a replayed pipeline returns the existing doc (one doc);
  rows with no pipeline are not constrained by the partial idempotency index;
  a run that does not exist (deleted before the lock) raises
  ``AIHistorySubjectUnavailable`` and leaves no doc.
* ``prune_run_versions`` keeps v1 and a version a published decision report
  cites, oldest-first otherwise, and prunes nothing when the citations cannot
  be read.

Uses the stores the app is configured with (``DATABASE_URL`` / ``MONGO_DB``,
set to throwaway databases by the W03 wrapper) because the subject lock opens
the app's own session. Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and Mongo.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.integration.ai_history_seed import drop_project, dsn, seed_project, seed_run

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def stores():
    dsn()
    from app.db import postgres as app_pg
    from app.db.mongo import get_mongo_db, reset_mongo_client

    await app_pg.dispose_engine_for_loop()
    reset_mongo_client()
    mongo = get_mongo_db()
    try:
        await mongo.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MongoDB is not reachable: {exc}")
    yield mongo, app_pg.AsyncSessionLocal
    reset_mongo_client()
    await app_pg.dispose_engine_for_loop()


@pytest.fixture
async def run(stores):
    mongo, session_factory = stores
    async with session_factory() as db:
        project = await seed_project(db, "gah-mongo")
        run_id, _ = await seed_run(db, project, 1)
        await db.commit()
    yield project, run_id
    from app.db.mongo import Collections

    await mongo[Collections.AI_RUN_VERSIONS].delete_many({"test_run_id": str(run_id)})
    await mongo[Collections.DECISION_REPORTS].delete_many({"test_run_id": str(run_id)})
    await drop_project(session_factory, project)


def _payload(n: int) -> dict:
    return {"layer1_executive_summary": f"summary {n}", "fallback_used": False, "executive_summary": f"summary {n}"}


async def _record(mongo, project, run_id, *, n=1, pipeline="p", kind="run_summary", caps=None):
    from app.services.ai_history_core import meta_from_summary, provenance_from_summary
    from app.services.ai_history_service import record_run_version

    payload = _payload(n)
    return await record_run_version(
        mongo, test_run_id=str(run_id), project_id=str(project), kind=kind, payload=payload,
        meta=meta_from_summary(payload), provenance=provenance_from_summary(payload),
        pipeline_run_id=pipeline, caps=caps,
    )


async def test_ensure_indexes_creates_the_four_and_leaves_run_summaries_alone(stores):
    from app.db.mongo import Collections, ensure_indexes

    mongo, _ = stores
    await ensure_indexes()
    info = await mongo[Collections.AI_RUN_VERSIONS].index_information()
    by_key = {tuple(spec["key"]): spec for name, spec in info.items() if name != "_id_"}
    assert by_key[(("test_run_id", 1), ("kind", 1), ("version", 1))]["unique"] is True
    assert by_key[(("test_run_id", 1), ("version_id", 1))]["unique"] is True
    partial = by_key[(("test_run_id", 1), ("kind", 1), ("pipeline_run_id", 1), ("content_sha256", 1))]
    assert partial["unique"] is True
    assert partial["partialFilterExpression"] == {"pipeline_run_id": {"$type": "string"}}
    assert "unique" not in by_key[(("project_id", 1), ("generated_at", -1))]
    summaries = await mongo[Collections.RUN_SUMMARIES].index_information()
    (unique,) = [spec for name, spec in summaries.items() if spec["key"] == [("test_run_id", 1)]]
    assert unique["unique"] is True
    assert set(summaries) == {"_id_", "test_run_id_1"}


async def test_five_concurrent_writers_get_versions_one_to_five(stores, run):
    from app.db.mongo import Collections, ensure_indexes

    mongo, _ = stores
    project, run_id = run
    await ensure_indexes()
    results = await asyncio.gather(*[
        _record(mongo, project, run_id, n=i, pipeline=f"pipe-{i}") for i in range(5)
    ])
    assert sorted(r.version for r in results) == [1, 2, 3, 4, 5]
    assert all(r.created for r in results)
    docs = await mongo[Collections.AI_RUN_VERSIONS].find(
        {"test_run_id": str(run_id)}, {"_id": 0, "version": 1, "version_id": 1}
    ).to_list(length=None)
    assert sorted(d["version"] for d in docs) == [1, 2, 3, 4, 5]
    assert len({d["version_id"] for d in docs}) == 5


async def test_a_replayed_pipeline_returns_the_existing_doc(stores, run):
    from app.db.mongo import Collections, ensure_indexes

    mongo, _ = stores
    project, run_id = run
    await ensure_indexes()
    first = await _record(mongo, project, run_id, pipeline="pipe-a")
    again = await _record(mongo, project, run_id, pipeline="pipe-a")
    assert first.created and not again.created and again.version_id == first.version_id
    assert "_id" not in again.doc
    assert await mongo[Collections.AI_RUN_VERSIONS].count_documents({"test_run_id": str(run_id)}) == 1


async def test_rows_without_a_pipeline_are_not_constrained_by_the_partial_index(stores, run):
    from app.db.mongo import ensure_indexes

    mongo, _ = stores
    project, run_id = run
    await ensure_indexes()
    a = await _record(mongo, project, run_id, pipeline=None)
    b = await _record(mongo, project, run_id, pipeline=None)
    assert (a.version, b.version) == (1, 2) and a.created and b.created


async def test_a_run_gone_before_the_lock_leaves_no_orphan(stores):
    from app.db.mongo import Collections
    from app.services.ai_history_service import AIHistorySubjectUnavailable

    mongo, _ = stores
    ghost = uuid.uuid4()
    with pytest.raises(AIHistorySubjectUnavailable):
        await _record(mongo, uuid.uuid4(), ghost, pipeline="pipe-x")
    assert await mongo[Collections.AI_RUN_VERSIONS].count_documents({"test_run_id": str(ghost)}) == 0


async def test_prune_keeps_v1_and_the_cited_version(stores, run):
    from app.db.mongo import Collections
    from app.services.ai_history_core import AIHistoryCaps
    from app.services.ai_history_service import prune_run_versions

    mongo, _ = stores
    project, run_id = run
    await mongo[Collections.DECISION_REPORTS].insert_one(
        {"report_id": str(uuid.uuid4()), "test_run_id": str(run_id), "project_id": str(project),
         "pipeline_run_id": "pipe-2", "report_version": 1, "status": "published"}
    )
    for i in range(1, 6):
        await _record(mongo, project, run_id, n=i, pipeline=f"pipe-{i}", caps=AIHistoryCaps(per_run=3))
    left = sorted(d["version"] for d in await mongo[Collections.AI_RUN_VERSIONS].find(
        {"test_run_id": str(run_id)}, {"version": 1}
    ).to_list(length=None))
    assert left == [1, 2, 5], "v1 and the cited v2 stay; v3 and v4 went oldest-first"
    assert await prune_run_versions(mongo, test_run_id=str(run_id), kind="run_summary", cap=3) == 0


async def test_backfill_versions_each_existing_run_once(stores):
    """``backfill_chunk`` (the one-shot task's unit): a v1 ``run_summary`` from
    the ``run_summaries`` doc (without its overwritten markdown) and a v1
    ``analysis_digest`` of the current data, both ``trigger=backfill``; a run
    with nothing to version gets nothing; a run a live pipeline already
    versioned keeps only that version; a second pass writes nothing."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from app.db.mongo import Collections
    from app.models.postgres import TestRun
    from app.services.ai_history_service import backfill_chunk, capture_analysis_digest
    from tests.integration.ai_history_seed import seed_analysis, seed_pipeline

    mongo, factory = stores
    far = datetime(2099, 1, 1, tzinfo=timezone.utc)
    analysed_at = datetime(2026, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
    async with factory() as db:
        project = await seed_project(db, "gah-backfill-mongo")
        run_a, cases_a = await seed_run(db, project, 2)
        run_b, _ = await seed_run(db, project, 1)
        run_c, cases_c = await seed_run(db, project, 1)
        for i, c in enumerate(cases_a):
            await seed_analysis(db, c, created_at=analysed_at - timedelta(hours=i))
        await seed_analysis(db, cases_c[0])
        pipeline_a = await seed_pipeline(db, run_a)
        pipeline_c = await seed_pipeline(db, run_c)
        await db.execute(update(TestRun).where(TestRun.id.in_([run_a, run_b, run_c])).values(created_at=far))
        await db.commit()
    runs = [str(r) for r in (run_a, run_b, run_c)]
    try:
        await mongo[Collections.RUN_SUMMARIES].insert_one({
            "test_run_id": str(run_a), "project_id": str(project), "layer1_executive_summary": "Two failures",
            "executive_summary": "Two failures", "markdown_report": "# the DECISION report",
            "fallback_used": False, "analysis_count": 2, "generated_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
        })
        async with factory() as db:  # run C was versioned by a live pipeline already
            live = await capture_analysis_digest(db, mongo, run_id=run_c, pipeline_run_id=pipeline_c)
            await db.commit()
        cursor = (far - timedelta(days=1), uuid.UUID(int=0))
        async with factory() as db:
            first = await backfill_chunk(db, mongo, cursor, limit=10)
            again = await backfill_chunk(db, mongo, cursor, limit=10)
        docs = await mongo[Collections.AI_RUN_VERSIONS].find(
            {"test_run_id": {"$in": runs}}, {"_id": 0}
        ).to_list(length=None)
    finally:
        await mongo[Collections.AI_RUN_VERSIONS].delete_many({"test_run_id": {"$in": runs}})
        await mongo[Collections.RUN_SUMMARIES].delete_many({"test_run_id": {"$in": runs}})
        await drop_project(factory, project)

    assert live.version.created
    assert (first.scanned, first.written, first.done) == (3, 2, True) and first.errors == []
    assert again.written == 0 and again.errors == []
    by = {(d["test_run_id"], d["kind"]): d for d in docs}
    assert set(by) == {(str(run_a), "run_summary"), (str(run_a), "analysis_digest"), (str(run_c), "analysis_digest")}
    summary = by[(str(run_a), "run_summary")]
    assert (summary["version"], summary["trigger"], summary["pipeline_run_id"]) == (1, "backfill", None)
    assert summary["payload"]["markdown_report"] is None
    assert summary["payload"]["markdown_source"] == "overwritten_by_decision_report"
    assert summary["meta"]["headline"] == "Two failures"
    digest = by[(str(run_a), "analysis_digest")]
    assert (digest["version"], digest["trigger"], digest["pipeline_run_id"]) == (1, "backfill", str(pipeline_a))
    assert digest["payload"]["provenance"]["source"] == "stored" and digest["payload"]["analysed_tests"] == 2
    assert digest["generated_at"].replace(tzinfo=None) == analysed_at.replace(tzinfo=None), "max(ai_analysis.created_at)"
    assert by[(str(run_c), "analysis_digest")]["trigger"] == "pipeline"


async def test_prune_does_nothing_when_citations_cannot_be_read(stores, run, monkeypatch):
    from app.db.mongo import Collections
    from app.services import ai_history_service as svc

    mongo, _ = stores
    project, run_id = run
    for i in range(1, 6):
        await _record(mongo, project, run_id, n=i, pipeline=f"pipe-{i}")

    async def unreadable(*_a, **_k):
        return None

    monkeypatch.setattr(svc, "cited_pipeline_ids", unreadable)
    assert await svc.prune_run_versions(mongo, test_run_id=str(run_id), kind="run_summary", cap=1) == 0
    assert await mongo[Collections.AI_RUN_VERSIONS].count_documents({"test_run_id": str(run_id)}) == 5
