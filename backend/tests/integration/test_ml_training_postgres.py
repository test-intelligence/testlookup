"""W04 AIC-05: the ML training task end to end, against a real PostgreSQL and a real Redis.

What the fakes in ``tests/test_ml_training_job.py`` / ``test_ml_training_routes.py``
cannot show:

1. **The status counts are the trainer's counts.** ``training_job.count_training_samples``
   runs on the request session (a second session stalls the 2+1 pool); the
   trainer's ``get_training_sample_count`` / ``get_human_label_count`` open
   their own. Same SQL text; this proves the numbers agree on a real database,
   and that 60 seeded human labels move both by exactly 60.
2. **The real task trains on real rows and audits.** ``train_ml_classifier``
   driven through the worker loop runner (``.apply``) gathers the seeded
   feedback with the trainer's real SQL, trains, writes the ``.joblib``, and
   commits a ``settings_audit_log`` row ``ml_classifier / trained``.
3. **The compare-and-delete Lua runs on Redis.** The lock the route took for
   the task is gone after it; a lock held by another task id survives a
   release with the wrong id.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (migrated to head) and a reachable
``REDIS_URL`` (skipped otherwise). Every row is written under a fresh project
and removed at the end, the audit row included (the databases are shared).
Local: ``bash docs/deferred-scope-2026-10-13/tools/local-stack/pg_pytest_w04.sh d <log> --
tests/integration/test_ml_training_postgres.py``.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytest.importorskip("asyncpg")
pytest.importorskip("sklearn")

pytestmark = pytest.mark.integration

TASK = str(uuid.uuid4())
OTHER = str(uuid.uuid4())

# Three cleanly separable failure shapes -> three categories.
_SHAPES = (
    ("Timeout after 30000ms waiting for connection pool", "INFRASTRUCTURE"),
    ("AssertionError: expected 200 but was 500", "PRODUCT_BUG"),
    ("NoSuchElementException: element #checkout not found", "AUTOMATION_DEFECT"),
)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
def worker(monkeypatch, tmp_path):
    """The app's engine and Redis client on the worker loop runner, pointed at the test stores."""
    import app.db.postgres as pg
    from app.core.config import settings
    from app.worker import loop_runner

    monkeypatch.setattr(settings, "DATABASE_URL", _dsn())
    monkeypatch.setattr(settings, "ML_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ML_MODEL_SYNC_ENABLED", False)
    monkeypatch.setattr(settings, "ML_MIN_TRAINING_SAMPLES", 20)
    monkeypatch.setattr(settings, "ML_ACCURACY_THRESHOLD", 0.0)
    loop_runner.shutdown_worker_loop()
    pg.get_engine.cache_clear()
    pg.get_session_factory.cache_clear()

    async def _ping():
        from app.db.redis_client import get_redis

        await get_redis().ping()

    try:
        loop_runner.run_async(_ping())
    except Exception as exc:  # noqa: BLE001
        loop_runner.shutdown_worker_loop()
        pytest.skip(f"Redis is not reachable at REDIS_URL: {exc}")
    try:
        yield loop_runner.run_async
    finally:
        loop_runner.shutdown_worker_loop()
        pg.get_engine.cache_clear()
        pg.get_session_factory.cache_clear()


async def _seed(n: int = 60) -> dict:
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import AIAnalysis, AIFeedback, Project, TestCase, TestRun

    tag = uuid.uuid4().hex[:10]
    ids = {"project": uuid.uuid4(), "run": uuid.uuid4(), "cases": [], "analyses": []}
    async with AsyncSessionLocal() as db:
        db.add(Project(id=ids["project"], name=f"mlt-{tag}", slug=f"mlt-{tag}", is_active=True))
        await db.flush()
        db.add(TestRun(id=ids["run"], project_id=ids["project"], build_number=f"mlt-{tag}", jenkins_job="mlt",
                       status="FAILED", ingestion_source="upload", branch="main", primary_suite_name="checkout"))
        await db.flush()
        for i in range(n):
            message, category = _SHAPES[i % 3]
            case_id, analysis_id = uuid.uuid4(), uuid.uuid4()
            ids["cases"].append(case_id)
            ids["analyses"].append(analysis_id)
            db.add(TestCase(id=case_id, test_run_id=ids["run"], test_name=f"test_{tag}_{i}", class_name="checkout",
                            suite_name="checkout", test_fingerprint=f"{tag}{i:054d}", status="FAILED",
                            duration_ms=100 + i, error_message=f"{message} (#{i})"))
        await db.flush()
        for i, (case_id, analysis_id) in enumerate(zip(ids["cases"], ids["analyses"])):
            db.add(AIAnalysis(id=analysis_id, test_case_id=case_id, failure_category=_SHAPES[i % 3][1],
                              confidence_score=60))
        await db.flush()
        for case_id, analysis_id in zip(ids["cases"], ids["analyses"]):
            db.add(AIFeedback(analysis_id=analysis_id, test_case_id=case_id, rating="correct", source="manual"))
        await db.commit()
    return ids


async def _cleanup(ids: dict, version: str | None) -> None:
    from sqlalchemy import text

    from app.db.postgres import AsyncSessionLocal
    from app.db.redis_client import get_redis
    from app.services.ml import training_job as job

    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM ai_feedback WHERE test_case_id = ANY(:ids)"), {"ids": ids["cases"]})
        await db.execute(text("DELETE FROM ai_analysis WHERE test_case_id = ANY(:ids)"), {"ids": ids["cases"]})
        await db.execute(text("DELETE FROM test_cases WHERE test_run_id = :run"), {"run": ids["run"]})
        await db.execute(text("DELETE FROM test_runs WHERE id = :run"), {"run": ids["run"]})
        await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": ids["project"]})
        if version:
            await db.execute(
                text("DELETE FROM settings_audit_log WHERE setting_key = 'ml_classifier' "
                     "AND CAST(changed_fields AS TEXT) LIKE :pattern"),
                {"pattern": f'%"version:{version}"%'},
            )
        await db.commit()
    await get_redis().delete(job.LOCK_KEY, job.LAST_RUN_KEY)


async def _counts() -> tuple[tuple[int, int], tuple[int, int]]:
    from app.db.postgres import AsyncSessionLocal
    from app.services.ml import trainer
    from app.services.ml import training_job as job

    async with AsyncSessionLocal() as db:
        ours = await job.count_training_samples(db)
    theirs = (await trainer.get_training_sample_count(), await trainer.get_human_label_count())
    return ours, theirs


def test_counts_agree_with_the_trainer_and_the_task_trains_and_audits(worker):
    from app.services.ml import training_job as job
    from app.services.ml.classifier import MLClassifier
    from app.worker import tasks

    run = worker
    before, before_trainer = run(_counts())
    assert before == before_trainer
    ids = run(_seed())
    version = None
    try:
        after, after_trainer = run(_counts())
        assert after == after_trainer
        assert (after[0] - before[0], after[1] - before[1]) == (60, 60)

        async def _take_lock():
            from app.db.redis_client import get_redis

            await get_redis().delete(job.LOCK_KEY, job.LAST_RUN_KEY)
            return await job.acquire_lock(get_redis(), task_id=TASK, triggered_by="user:alice")

        assert run(_take_lock())

        result = tasks.train_ml_classifier.apply(kwargs={"triggered_by": "user:alice"}, task_id=TASK).get()

        assert result["status"] == "trained", result
        version = result["version"]
        assert MLClassifier.is_available()

        async def _after():
            from sqlalchemy import text

            from app.db.postgres import AsyncSessionLocal
            from app.db.redis_client import get_redis

            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    text("SELECT action, actor_id, changed_fields FROM settings_audit_log "
                         "WHERE setting_key = 'ml_classifier' AND CAST(changed_fields AS TEXT) LIKE :pattern"),
                    {"pattern": f'%"version:{version}"%'},
                )).all()
            redis = get_redis()
            return rows, await redis.get(job.LOCK_KEY), await redis.get(job.LAST_RUN_KEY)

        rows, lock, last = run(_after())
        assert len(rows) == 1
        action, actor_id, fields = rows[0]
        assert (action, actor_id) == ("trained", None)
        assert fields[0] == f"version:{version}" and fields[-1] == "triggered_by:user:alice"
        assert lock is None  # the real compare-and-delete freed the task's own lock
        last = json.loads(last)
        assert (last["status"], last["version"], last["triggered_by"]) == ("trained", version, "user:alice")
    finally:
        run(_cleanup(ids, version))


def test_the_release_lua_frees_only_the_holders_lock(worker):
    from app.services.ml import training_job as job

    async def _scenario():
        from app.db.redis_client import get_redis

        redis = get_redis()
        await redis.delete(job.LOCK_KEY)
        try:
            assert await job.acquire_lock(redis, task_id=OTHER, triggered_by="beat")
            assert not await job.acquire_lock(redis, task_id=TASK, triggered_by="user:alice")  # NX
            ttl = await redis.ttl(job.LOCK_KEY)
            assert 3500 < ttl <= 3600
            assert await job.release_lock(redis, TASK) is False
            assert (await job.read_lock(redis))["task_id"] == OTHER
            held = await job.read_lock(redis)
            await job.mark_lock_started(redis, held)
            assert (await job.read_lock(redis))["started_at"]
            assert 3500 < await redis.ttl(job.LOCK_KEY) <= 3600  # KEEPTTL
            assert await job.release_lock(redis, OTHER) is True
            assert await redis.get(job.LOCK_KEY) is None
        finally:
            await redis.delete(job.LOCK_KEY)

    worker(_scenario())
