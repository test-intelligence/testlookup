"""W04 AIC-05: one ML-classifier training job (``services/ml/training_job``) and its Celery task.

The trainer had no caller: its docstring named a nightly beat and a retrain
route that did not exist. What this file pins (contract §2.9, assertions 4-6):

* a run over a separable 3-category pool trains with the REAL trainer, writes
  the ``.joblib`` + ``training_metadata.json``, publishes them to the store,
  makes ``MLClassifier.is_available()`` true and ``auto`` resolve to ML with the
  documented reason, records ``ml:retrain:last`` and hands the task the
  ``ml_classifier / trained`` audit row;
* below the accuracy threshold / below the sample floor nothing is written, the
  previous model is kept, the outcome is recorded and no audit row is asked for;
* the nightly run obeys ``ML_RETRAIN_ENABLED``, a manual run ignores it;
* the lock: a nightly run skips while a manual one holds it, a job stamps
  ``started_at`` on its own lock and frees only its own (compare-and-delete);
* the job never raises, and the Celery task writes the audit row and commits;
* the beat entry fires at 03:10 UTC on the ``default`` queue.

Redis is an in-memory fake; the compare-and-delete Lua itself runs against a
real Redis in ``tests/integration/test_ml_training_postgres.py``.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.ml import training_job as job

TASK = "11111111-1111-4111-8111-111111111111"
OTHER_TASK = "22222222-2222-4222-8222-222222222222"


class FakeRedis:
    """The few commands the job uses, with NX/XX/EX/KEEPTTL semantics."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.sets: list[tuple[str, dict]] = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, *, nx=False, xx=False, ex=None, keepttl=False):
        self.sets.append((key, {"nx": nx, "xx": xx, "ex": ex, "keepttl": keepttl}))
        if nx and key in self.store:
            return None
        if xx and key not in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttl[key] = ex
        elif not keepttl:
            self.ttl.pop(key, None)
        return True

    async def eval(self, script, numkeys, *args):
        assert script == job._RELEASE_LOCK_LUA and numkeys == 1
        key, task_id = args
        raw = self.store.get(key)
        try:
            decoded = json.loads(raw) if raw is not None else None
        except ValueError:
            decoded = None
        if isinstance(decoded, dict) and decoded.get("task_id") == task_id:
            del self.store[key]
            self.ttl.pop(key, None)
            return 1
        return 0

    def lock(self):
        raw = self.store.get(job.LOCK_KEY)
        return json.loads(raw) if raw else None

    def last_run(self):
        raw = self.store.get(job.LAST_RUN_KEY)
        return json.loads(raw) if raw else None


class _FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put_object(self, key, content, content_type=None):
        self.objects[key] = content

    async def list_objects(self, prefix):
        return [{"key": k, "size": len(v)} for k, v in self.objects.items() if k.startswith(prefix)]

    async def get_object_content(self, key):
        return self.objects[key]


def _separable_pool(n: int = 60):
    """Three cleanly separable categories, all human labels (``human_direct``)."""
    from app.services.ml.feature_extractor import FEATURE_NAMES
    from app.services.ml.label_provenance import HUMAN_DIRECT

    keyword = {0: ("has_timeout_keyword", "INFRASTRUCTURE"), 1: ("has_assertion_keyword", "PRODUCT_BUG"),
               2: ("has_element_keyword", "AUTOMATION_DEFECT")}
    assert all(feature in FEATURE_NAMES for feature, _ in keyword.values())
    samples, labels, provenances = [], [], []
    for i in range(n):
        feature, label = keyword[i % 3]
        s = {name: 0.0 for name in FEATURE_NAMES}
        s[feature] = 1.0
        s["error_msg_length"] = float(i % 7)
        samples.append(s)
        labels.append(label)
        provenances.append(HUMAN_DIRECT)
    return samples, labels, provenances


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def model_env(monkeypatch, tmp_path):
    """A tmp ML_MODEL_DIR, the store on (fake), the trainer fed the separable pool."""
    from app.core.config import settings
    from app.services.ml import model_store, trainer

    storage = _FakeStorage()
    monkeypatch.setattr(settings, "ML_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ML_MODEL_SYNC_ENABLED", True)
    monkeypatch.setattr(settings, "ML_MIN_TRAINING_SAMPLES", 20)
    monkeypatch.setattr(settings, "ML_ACCURACY_THRESHOLD", 0.0)
    monkeypatch.setattr(settings, "ML_RETRAIN_ENABLED", True)
    monkeypatch.setattr(model_store, "_storage", lambda: storage)
    pool = _separable_pool()

    async def _gather():
        return pool

    monkeypatch.setattr(trainer, "_gather_training_data", _gather)
    return SimpleNamespace(dir=tmp_path, storage=storage, settings=settings, trainer=trainer)


def _stub_trainer(monkeypatch, result=None, exc=None):
    from app.services.ml import trainer

    calls = []

    async def _train():
        calls.append(1)
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(trainer, "train_classifier", _train)
    return calls


# ── assertion 4: a real training run ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_manual_run_trains_publishes_and_makes_auto_prefer_ml(redis, model_env, monkeypatch):
    pytest.importorskip("sklearn")
    from app.services import analysis_router
    from app.services.ml.classifier import MLClassifier

    # The route took the lock for this task before queueing it.
    assert await job.acquire_lock(redis, task_id=TASK, triggered_by="user:alice")
    assert not MLClassifier.is_available()

    outcome = await job.run_training_job(TASK, "user:alice", redis=redis)

    assert outcome.result["status"] == "trained", outcome.result
    version = outcome.result["version"]
    assert (model_env.dir / f"classifier_v{version}.joblib").exists()
    meta = json.loads((model_env.dir / "training_metadata.json").read_text())
    assert meta["version"] == version and meta["sample_count"] == 60
    # model_store.publish ran: both files are in the (fake) object store.
    assert set(model_env.storage.objects) == {
        f"ml-models/classifier_v{version}.joblib", "ml-models/training_metadata.json",
    }
    assert MLClassifier.is_available()
    monkeypatch.setattr(analysis_router, "_cached_mode", None)
    assert analysis_router.resolve_analysis_mode_with_reason(configured="auto") == (
        "ml", "auto: trained ML model available — preferred over LLM",
    )

    last = redis.last_run()
    assert last["status"] == "trained" and last["version"] == version
    assert last["triggered_by"] == "user:alice" and last["sample_count"] == 60
    assert 0.0 <= last["accuracy"] <= 1.0
    assert redis.ttl[job.LAST_RUN_KEY] == job.LAST_RUN_TTL_SECONDS
    assert outcome.audit == {
        "setting_key": "ml_classifier",
        "action": "trained",
        "changed_fields": [
            f"version:{version}",
            f"accuracy:{outcome.result['accuracy']:.4f}",
            "samples:60",
            "triggered_by:user:alice",
        ],
    }
    # The lock is released; the result is plain JSON for the Celery backend.
    assert redis.lock() is None
    json.dumps(outcome.result)


@pytest.mark.asyncio
async def test_the_job_stamps_started_at_on_its_own_lock_keeping_the_ttl(redis, monkeypatch):
    seen = {}

    async def _train():
        seen["lock"] = redis.lock()
        return {"status": "insufficient_data", "message": "Need 20 samples, have 3.", "sample_count": 3}

    from app.services.ml import trainer

    monkeypatch.setattr(trainer, "train_classifier", _train)
    assert await job.acquire_lock(redis, task_id=TASK, triggered_by="user:alice")
    await job.run_training_job(TASK, "user:alice", redis=redis)

    assert seen["lock"]["task_id"] == TASK and seen["lock"]["started_at"]
    stamp = [opts for key, opts in redis.sets if key == job.LOCK_KEY][-1]
    assert stamp == {"nx": False, "xx": True, "ex": None, "keepttl": True}


# ── assertion 5: below threshold / insufficient data ─────────────────────────


@pytest.mark.asyncio
async def test_below_threshold_keeps_the_previous_model_and_writes_no_audit(redis, model_env, monkeypatch):
    pytest.importorskip("sklearn")
    previous = model_env.dir / "classifier_v20260101_000000.joblib"
    previous.write_bytes(b"previous model")
    (model_env.dir / "training_metadata.json").write_text(json.dumps({"status": "trained", "version": "20260101_000000"}))
    monkeypatch.setattr(model_env.settings, "ML_ACCURACY_THRESHOLD", 1.01)  # unreachable

    outcome = await job.run_training_job(TASK, "beat", redis=redis)

    assert outcome.result["status"] == "below_threshold"
    assert outcome.audit is None
    assert sorted(p.name for p in model_env.dir.glob("classifier_v*.joblib")) == [previous.name]
    assert json.loads((model_env.dir / "training_metadata.json").read_text())["version"] == "20260101_000000"
    assert model_env.storage.objects == {}
    last = redis.last_run()
    assert last["status"] == "below_threshold" and last["version"] is None
    assert last["message"].startswith("Accuracy ") and "below the deployment threshold 1.01" in last["message"]


@pytest.mark.asyncio
async def test_insufficient_data_is_recorded_without_a_model_or_an_audit(redis, model_env, monkeypatch):
    monkeypatch.setattr(model_env.settings, "ML_MIN_TRAINING_SAMPLES", 500)

    outcome = await job.run_training_job(TASK, "user:alice", redis=redis)

    assert outcome.result["status"] == "insufficient_data"
    assert outcome.audit is None
    assert list(model_env.dir.glob("classifier_v*.joblib")) == []
    last = redis.last_run()
    assert last["status"] == "insufficient_data"
    assert last["message"] == "Need 500 samples, have 60."
    assert last["sample_count"] == 60


# ── assertion 6: ML_RETRAIN_ENABLED gates the nightly run only ───────────────


@pytest.mark.asyncio
async def test_the_nightly_run_obeys_ml_retrain_enabled(redis, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ML_RETRAIN_ENABLED", False)
    calls = _stub_trainer(monkeypatch, result={"status": "trained"})

    outcome = await job.run_training_job(TASK, "beat", redis=redis)

    assert outcome.result == {"status": "disabled", "message": job.DISABLED_MESSAGE}
    assert calls == []
    assert redis.last_run()["status"] == "disabled"
    assert redis.lock() is None  # a disabled night never takes the lock


@pytest.mark.asyncio
async def test_a_manual_run_ignores_ml_retrain_enabled(redis, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ML_RETRAIN_ENABLED", False)
    calls = _stub_trainer(monkeypatch, result={"status": "insufficient_data", "message": "m", "sample_count": 1})

    outcome = await job.run_training_job(TASK, "user:alice", redis=redis)

    assert calls == [1]
    assert outcome.result["status"] == "insufficient_data"


# ── the lock ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_nightly_run_skips_while_a_manual_run_holds_the_lock(redis, monkeypatch):
    calls = _stub_trainer(monkeypatch, result={"status": "trained"})
    assert await job.acquire_lock(redis, task_id=OTHER_TASK, triggered_by="user:alice")

    outcome = await job.run_training_job(TASK, "beat", redis=redis)

    assert outcome.result["status"] == "skipped_running"
    assert outcome.result["running_task_id"] == OTHER_TASK
    assert outcome.audit is None
    assert calls == []
    # The manual run's lock is untouched: a job never frees a lock it does not hold.
    assert redis.lock()["task_id"] == OTHER_TASK
    assert redis.last_run()["status"] == "skipped_running"


@pytest.mark.asyncio
async def test_a_nightly_run_takes_the_lock_while_it_trains(redis, monkeypatch):
    seen = {}

    async def _train():
        seen["lock"] = redis.lock()
        return {"status": "insufficient_data", "message": "m", "sample_count": 1}

    from app.services.ml import trainer

    monkeypatch.setattr(trainer, "train_classifier", _train)
    await job.run_training_job(TASK, "beat", redis=redis)

    assert seen["lock"]["task_id"] == TASK and seen["lock"]["triggered_by"] == "beat"
    assert seen["lock"]["started_at"]
    assert redis.ttl.get(job.LOCK_KEY) is None and redis.lock() is None  # released after
    taken = [opts for key, opts in redis.sets if key == job.LOCK_KEY][0]
    assert taken == {"nx": True, "xx": False, "ex": job.LOCK_TTL_SECONDS, "keepttl": False}


@pytest.mark.asyncio
async def test_release_is_a_compare_and_delete(redis):
    assert await job.acquire_lock(redis, task_id=OTHER_TASK, triggered_by="beat")
    assert await job.release_lock(redis, TASK) is False
    assert redis.lock()["task_id"] == OTHER_TASK
    assert await job.release_lock(redis, OTHER_TASK) is True
    assert redis.lock() is None


# ── never raises ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_trainer_crash_is_recorded_as_error_and_frees_the_lock(redis, monkeypatch):
    _stub_trainer(monkeypatch, exc=RuntimeError("disk full " + "x" * 400))
    assert await job.acquire_lock(redis, task_id=TASK, triggered_by="user:alice")

    outcome = await job.run_training_job(TASK, "user:alice", redis=redis)

    assert outcome.result["status"] == "error"
    assert outcome.result["message"].startswith("disk full") and len(outcome.result["message"]) == 300
    assert outcome.audit is None
    assert redis.lock() is None
    assert redis.last_run()["status"] == "error"


@pytest.mark.asyncio
async def test_a_redis_outage_does_not_stop_the_training(monkeypatch):
    class _Down:
        async def get(self, *a, **k):
            raise ConnectionError("redis down")

        async def set(self, *a, **k):
            raise ConnectionError("redis down")

        async def eval(self, *a, **k):
            raise ConnectionError("redis down")

    calls = _stub_trainer(monkeypatch, result={"status": "trained", "version": "20261014_031000",
                                               "accuracy": 0.9, "sample_count": 250})
    outcome = await job.run_training_job(TASK, "beat", redis=_Down())

    assert calls == [1]
    assert outcome.result["status"] == "trained"
    assert outcome.audit is not None


# ── the Celery task: runs the job, writes the audit row, commits ─────────────


@pytest.fixture
def worker_loop():
    """``.apply()`` runs the task on the worker loop runner; tear that loop down after."""
    from app.worker import loop_runner

    yield
    loop_runner.shutdown_worker_loop()


class _AuditSession:
    def __init__(self, sink):
        self.sink = sink
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.sink.extend(self.added)


def test_the_task_writes_the_audit_row_and_commits(monkeypatch, worker_loop):
    from app.db import postgres as pg
    from app.models.postgres import SettingsAuditLog
    from app.worker import tasks

    committed: list = []
    received = {}

    async def _job(task_id, triggered_by):
        received.update(task_id=task_id, triggered_by=triggered_by)
        return job.TrainingJobOutcome(
            result={"status": "trained", "version": "20261014_031000", "accuracy": 0.84, "sample_count": 642},
            audit={"setting_key": "ml_classifier", "action": "trained",
                   "changed_fields": ["version:20261014_031000", "accuracy:0.8400", "samples:642",
                                      "triggered_by:user:alice"]},
        )

    monkeypatch.setattr(job, "run_training_job", _job)
    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _AuditSession(committed))

    result = tasks.train_ml_classifier.apply(kwargs={"triggered_by": "user:alice"}, task_id=TASK).get()

    assert result["status"] == "trained"
    assert received == {"task_id": TASK, "triggered_by": "user:alice"}
    assert len(committed) == 1 and isinstance(committed[0], SettingsAuditLog)
    row = committed[0]
    assert (row.setting_key, row.action, row.actor_id) == ("ml_classifier", "trained", None)
    assert row.changed_fields[-1] == "triggered_by:user:alice"


def test_the_task_writes_no_audit_row_for_a_run_that_did_not_train(monkeypatch, worker_loop):
    from app.db import postgres as pg
    from app.worker import tasks

    async def _job(task_id, triggered_by):
        return job.TrainingJobOutcome(result={"status": "insufficient_data"}, audit=None)

    def _no_session():
        raise AssertionError("no audit session for a run that did not train")

    monkeypatch.setattr(job, "run_training_job", _job)
    monkeypatch.setattr(pg, "AsyncSessionLocal", _no_session)

    assert tasks.train_ml_classifier.apply(kwargs={"triggered_by": "beat"}).get() == {"status": "insufficient_data"}


def test_the_task_is_registered_with_its_limits():
    from app.worker import tasks
    from app.worker.celery_app import celery_app

    task = celery_app.tasks["app.worker.tasks.train_ml_classifier"]
    assert task.name == tasks.train_ml_classifier.name
    assert (task.max_retries, task.time_limit, task.soft_time_limit) == (0, 1800, 1500)
    assert task.queue == "default"


def test_the_nightly_beat_entry_runs_at_03_10_utc_on_the_default_queue():
    from celery.schedules import crontab

    from app.worker.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["nightly-ml-classifier-training"]
    assert entry["task"] == "app.worker.tasks.train_ml_classifier"
    assert entry["schedule"] == crontab(hour=3, minute=10)
    assert entry["kwargs"] == {"triggered_by": "beat"}
    # Clear of the finetune check (03:00) and the flaky-confidence retrain (03:30).
    others = {
        name: e["schedule"] for name, e in celery_app.conf.beat_schedule.items()
        if name in ("daily-finetune-trigger-check", "nightly-flaky-confidence-training")
    }
    assert crontab(hour=3, minute=10) not in others.values()
    route = celery_app.amqp.router.route({}, "app.worker.tasks.train_ml_classifier")
    assert route["queue"].name == "default"


# ── the trainer's SQL names real columns ─────────────────────────────────────


def test_every_column_the_trainer_and_the_status_counts_select_exists():
    """``_gather_training_data`` selected ``tr.started_at`` (``test_runs`` has
    ``start_time``): Postgres refused the query, the trainer swallowed it and
    every real training run ended "insufficient_data, have 0". The live proof is
    ``tests/integration/test_ml_training_postgres.py``; this DB-free guard runs
    everywhere. The aliases are the ones the trainer's queries use."""
    import inspect
    import re

    from app.models.postgres import AIAnalysis, AIFeedback, TestCase, TestRun
    from app.services.ml import trainer

    tables = {
        "tr": TestRun.__table__, "tc": TestCase.__table__, "tc_current": TestCase.__table__,
        "tc_hist": TestCase.__table__, "ai": AIAnalysis.__table__, "fb": AIFeedback.__table__,
    }
    sources = inspect.getsource(trainer) + job.HUMAN_LABEL_COUNT_SQL + job.PSEUDO_LABEL_COUNT_SQL
    referenced = set(re.findall(r"\b(tr|tc|tc_current|tc_hist|ai|fb)\.([a-z_]+)\b", sources))
    assert ("tr", "start_time") in referenced
    missing = sorted(f"{alias}.{col}" for alias, col in referenced if col not in tables[alias].c)
    assert missing == [], f"columns the ML SQL selects but the tables do not have: {missing}"
