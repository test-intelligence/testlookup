"""W04 AIC-05: ``GET /settings/ai/ml/status`` and ``POST /settings/ai/ml/retrain`` on fakes.

Contract §2.9 assertions 1-3 and 7, plus the degrade paths of §3.2:

1. RBAC: the POST is ADMIN only and refuses a project-bound API key; the GET is
   QA lead and above (the guard of ``GET /settings/ai``).
2. Below the sample floor the POST answers 409 with a machine-readable detail,
   queues nothing and takes no lock; the GET says why it cannot train.
3. Idempotency: a second press while the lock is held answers 202 with the same
   task id and ``already_running: true``; ``apply_async`` runs once. The lock
   carries a 3600 s TTL (a dead worker frees it within an hour).
7. A model another pod published reaches this pod through ``sync_down``.

Also: Redis or the database down -> the GET still answers 200 with
``status_unavailable``; the broker down -> 503 and the lock is freed; no
scikit-learn -> 409 ``deps_missing``; the GET writes nothing; both counts run on
the request session (no second ``AsyncSessionLocal``).
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.services.ml import training_job as job

BASE = "/api/v1/settings/ai/ml"


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.writes: list[tuple] = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, *, nx=False, xx=False, ex=None, keepttl=False):
        self.writes.append(("set", key))
        if nx and key in self.store:
            return None
        if xx and key not in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttl[key] = ex
        return True

    async def eval(self, script, numkeys, key, task_id):
        self.writes.append(("eval", key))
        raw = self.store.get(key)
        if raw is not None and json.loads(raw).get("task_id") == task_id:
            del self.store[key]
            return 1
        return 0


class DownRedis:
    async def get(self, *a, **k):
        raise ConnectionError("redis down")

    async def set(self, *a, **k):
        raise ConnectionError("redis down")

    async def eval(self, *a, **k):
        raise ConnectionError("redis down")


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class CountDB:
    """The request session: answers the two label counts and spies every write."""

    def __init__(self, state):
        self.state = state
        self.queries: list[str] = []
        self.writes: list[str] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.queries.append(sql)
        if self.state.db_down:
            raise ConnectionError("database down")
        if "FROM ai_analysis" in sql:
            return _Scalar(self.state.pseudo)
        if "FROM ai_feedback" in sql:
            return _Scalar(self.state.human)
        raise AssertionError(f"unexpected query: {sql[:200]}")

    def add(self, *_a):
        self.writes.append("add")

    async def flush(self):
        self.writes.append("flush")

    async def commit(self):
        self.writes.append("commit")

    async def rollback(self):
        pass


def _user(role: str, bound_project=None):
    user = SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, username="alice")
    if bound_project is not None:
        user._testlookup_api_key_project_id = bound_project
    return user


@pytest.fixture
def api(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.core.deps import get_current_active_user
    from app.db import postgres as pg
    from app.db import redis_client
    from app.db.postgres import get_db
    from app.routers import ml_training
    from app.services.ml import model_store
    from app.worker import tasks

    state = SimpleNamespace(user=_user("ADMIN"), human=57, pseudo=585, db_down=False, redis=FakeRedis())
    db = CountDB(state)

    async def _db():
        yield db

    app = FastAPI()
    app.include_router(ml_training.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    monkeypatch.setattr(redis_client, "get_redis", lambda: state.redis)
    monkeypatch.setattr(settings, "ML_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ML_MIN_TRAINING_SAMPLES", 200)
    monkeypatch.setattr(settings, "ML_MODEL_SYNC_ENABLED", False)
    model_store.reset_sync_clock()

    def _no_second_session(*_a, **_k):
        raise AssertionError("the routes use the request session only")

    monkeypatch.setattr(pg, "AsyncSessionLocal", _no_second_session)

    queued: list[dict] = []
    state.enqueue_error = None

    def _apply_async(*, kwargs=None, task_id=None, **_extra):
        if state.enqueue_error is not None:
            raise state.enqueue_error
        queued.append({"kwargs": kwargs, "task_id": task_id})
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(tasks.train_ml_classifier, "apply_async", _apply_async)
    return SimpleNamespace(http=TestClient(app, raise_server_exceptions=False), state=state, db=db,
                           queued=queued, dir=tmp_path, settings=settings)


# ── 1. RBAC ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["QA_LEAD", "QA_ENGINEER", "TESTER", "VIEWER"])
def test_retrain_is_admin_only(api, role):
    api.state.user = _user(role)
    assert api.http.post(f"{BASE}/retrain").status_code == 403
    assert api.queued == [] and api.state.redis.store == {}


def test_retrain_refuses_a_project_bound_api_key(api):
    api.state.user = _user("ADMIN", bound_project=uuid.uuid4())
    assert api.http.post(f"{BASE}/retrain").status_code == 403
    assert api.queued == []


def test_an_admin_queues_one_run(api):
    res = api.http.post(f"{BASE}/retrain")
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["state"] == "queued" and body["already_running"] is False
    assert api.queued == [{"kwargs": {"triggered_by": "user:alice"}, "task_id": body["task_id"]}]
    lock = json.loads(api.state.redis.store[job.LOCK_KEY])
    assert lock["task_id"] == body["task_id"] and lock["triggered_by"] == "user:alice" and lock["queued_at"]
    assert api.state.redis.ttl[job.LOCK_KEY] == 3600


@pytest.mark.parametrize("role,code", [("ADMIN", 200), ("QA_LEAD", 200), ("QA_ENGINEER", 403), ("VIEWER", 403)])
def test_status_is_qa_lead_and_above(api, role, code):
    api.state.user = _user(role)
    assert api.http.get(f"{BASE}/status").status_code == code


# ── 2. Below the floor ───────────────────────────────────────────────────────


def test_below_the_floor_the_press_is_refused_and_explained(api):
    api.state.human, api.state.pseudo = 40, 102
    res = api.http.post(f"{BASE}/retrain")
    assert res.status_code == 409
    assert res.json() == {"detail": {"code": "insufficient_samples", "message": "Need 200 labelled samples, have 142.",
                                     "have": 142, "need": 200}}
    assert api.queued == [] and job.LOCK_KEY not in api.state.redis.store

    status = api.http.get(f"{BASE}/status").json()
    assert status["can_train"] is False
    assert status["cannot_train_reason"] == "insufficient_samples"
    assert status["cannot_train_message"] == "Need 200 labelled samples, have 142."
    assert (status["available_samples"], status["human_labels"], status["min_training_samples"]) == (142, 40, 200)


def test_without_scikit_learn_the_press_is_refused(api, monkeypatch):
    monkeypatch.setattr(job, "deps_installed", lambda: False)
    res = api.http.post(f"{BASE}/retrain")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "deps_missing"
    assert api.queued == []
    status = api.http.get(f"{BASE}/status").json()
    assert (status["deps_installed"], status["can_train"], status["cannot_train_reason"]) == (False, False, "deps_missing")


# ── 3. Idempotency ───────────────────────────────────────────────────────────


def test_a_second_press_joins_the_running_job(api):
    first = api.http.post(f"{BASE}/retrain").json()
    counted = len(api.db.queries)
    # Even with the labels now below the floor: the running job is the answer, not a 409.
    api.state.human, api.state.pseudo = 1, 1
    second = api.http.post(f"{BASE}/retrain")
    assert second.status_code == 202
    assert second.json() == {"task_id": first["task_id"], "state": "queued", "already_running": True}
    assert len(api.queued) == 1
    assert len(api.db.queries) == counted  # a press during a run reads only the lock

    # Once the job stamps started_at, the same press reads "running".
    lock = json.loads(api.state.redis.store[job.LOCK_KEY])
    api.state.redis.store[job.LOCK_KEY] = json.dumps({**lock, "started_at": "2026-10-14T03:10:01+00:00"})
    third = api.http.post(f"{BASE}/retrain").json()
    assert third == {"task_id": first["task_id"], "state": "running", "already_running": True}
    assert len(api.queued) == 1

    status = api.http.get(f"{BASE}/status").json()
    assert status["training"]["state"] == "running"
    assert status["training"]["task_id"] == first["task_id"]
    assert status["training"]["triggered_by"] == "user:alice"


def test_a_broker_outage_answers_503_and_frees_the_lock(api):
    api.state.enqueue_error = ConnectionError("broker down")
    res = api.http.post(f"{BASE}/retrain")
    assert res.status_code == 503
    assert res.json() == {"detail": "Training queue unavailable"}
    assert job.LOCK_KEY not in api.state.redis.store


def test_a_redis_outage_answers_503_on_the_press(api):
    api.state.redis = DownRedis()
    res = api.http.post(f"{BASE}/retrain")
    assert res.status_code == 503 and res.json() == {"detail": "Training queue unavailable"}
    assert api.queued == []


# ── 7. A model another pod published ─────────────────────────────────────────


class _Store:
    def __init__(self, objects):
        self.objects = objects

    async def list_objects(self, prefix):
        return [{"key": k, "size": len(v)} for k, v in self.objects.items()]

    async def get_object_content(self, key):
        return self.objects[key]


def test_status_shows_a_model_another_pod_published(api, monkeypatch):
    from app.services.ml import model_store

    meta = {"status": "trained", "version": "20261014_031000", "accuracy": 0.84, "sample_count": 642,
            "trained_at": "2026-10-14T03:10:42+00:00",
            "label_composition": {"human_label_count": 12}}
    store = _Store({
        "ml-models/classifier_v20261014_031000.joblib": b"model-bytes",
        "ml-models/training_metadata.json": json.dumps(meta).encode(),
    })
    monkeypatch.setattr(api.settings, "ML_MODEL_SYNC_ENABLED", True)
    monkeypatch.setattr(model_store, "_storage", lambda: store)
    assert not list(api.dir.glob("classifier_v*.joblib"))

    status = api.http.get(f"{BASE}/status").json()

    assert status["model_available"] is True
    assert status["version"] == "20261014_031000"
    assert status["accuracy"] == 0.84
    assert status["model_sample_count"] == 642
    assert status["trained_at"].startswith("2026-10-14T03:10:42")
    assert status["maturity"] == "bootstrap_llm_imitating"  # 12 human labels < ML_HUMAN_LABEL_FLOOR (50)
    assert (api.dir / "classifier_v20261014_031000.joblib").read_bytes() == b"model-bytes"


def test_a_finished_run_forces_the_sync_past_the_throttle(api, monkeypatch):
    """The card polls every 3 s while training; the throttled sync (once a minute)
    would show "no model yet" for up to a minute after the job finished."""
    from app.services.ml import model_store

    calls: list[bool] = []

    async def _sync_down(*, force=False):
        calls.append(force)
        return []

    monkeypatch.setattr(model_store, "sync_down", _sync_down)
    api.state.redis.store[job.LAST_RUN_KEY] = json.dumps({
        "status": "trained", "message": "m", "finished_at": "2026-10-14T03:11:00+00:00",
        "triggered_by": "beat", "accuracy": 0.9, "version": "20261014_031000", "sample_count": 642,
    })
    api.http.get(f"{BASE}/status")
    assert calls == [True]

    (api.dir / "classifier_v20261014_031000.joblib").write_bytes(b"m")
    (api.dir / "training_metadata.json").write_text(json.dumps({"status": "trained", "version": "20261014_031000"}))
    api.http.get(f"{BASE}/status")
    assert calls == [True, False]  # this pod has it: back to the throttled sync


# ── the status payload ───────────────────────────────────────────────────────


def test_status_with_no_model_and_enough_samples(api):
    status = api.http.get(f"{BASE}/status").json()
    assert status == {
        "model_available": False, "version": None, "accuracy": None, "trained_at": None,
        "model_sample_count": None, "available_samples": 642, "min_training_samples": 200,
        "human_labels": 57, "human_label_floor": api.settings.ML_HUMAN_LABEL_FLOOR, "maturity": "not_trained",
        "deps_installed": job.deps_installed(), "retrain_enabled": api.settings.ML_RETRAIN_ENABLED,
        "can_train": job.deps_installed(), "cannot_train_reason": None if job.deps_installed() else "deps_missing",
        "cannot_train_message": None if job.deps_installed() else job.DEPS_MISSING_MESSAGE,
        "training": {"state": "idle", "task_id": None, "queued_at": None, "started_at": None, "triggered_by": None},
        "last_run": None,
    }


def test_status_carries_the_last_attempt(api):
    api.state.redis.store[job.LAST_RUN_KEY] = json.dumps({
        "status": "below_threshold", "message": "Accuracy 0.61 is below the deployment threshold 0.80; the previous model is kept.",
        "finished_at": "2026-10-14T03:11:00+00:00", "triggered_by": "beat", "accuracy": 0.61, "version": None,
        "sample_count": 642,
    })
    last = api.http.get(f"{BASE}/status").json()["last_run"]
    assert last["status"] == "below_threshold" and last["accuracy"] == 0.61
    assert last["finished_at"].startswith("2026-10-14T03:11:00")


def test_the_status_read_writes_nothing(api):
    api.http.get(f"{BASE}/status")
    assert api.db.writes == []
    assert api.state.redis.writes == []
    assert len(api.db.queries) == 2  # the two counts, on the request session


def test_a_redis_outage_degrades_the_status_instead_of_500(api):
    api.state.redis = DownRedis()
    res = api.http.get(f"{BASE}/status")
    assert res.status_code == 200
    body = res.json()
    assert body["can_train"] is False
    if job.deps_installed():
        assert body["cannot_train_reason"] == "status_unavailable"
    assert body["training"]["state"] == "idle" and body["last_run"] is None
    assert body["available_samples"] == 642  # the database part still reads


def test_a_database_outage_degrades_the_status_instead_of_500(api):
    api.state.db_down = True
    res = api.http.get(f"{BASE}/status")
    assert res.status_code == 200
    body = res.json()
    assert (body["available_samples"], body["human_labels"], body["can_train"]) == (0, 0, False)
    if job.deps_installed():
        assert body["cannot_train_reason"] == "status_unavailable"


def test_a_database_outage_answers_503_on_the_press(api):
    api.state.db_down = True
    res = api.http.post(f"{BASE}/retrain")
    assert res.status_code == 503
    assert api.queued == [] and job.LOCK_KEY not in api.state.redis.store


# ── wiring ───────────────────────────────────────────────────────────────────


def test_the_router_is_registered_right_after_app_settings():
    from app.bootstrap import PROTECTED_ROUTERS
    from app.routers import app_settings, ml_training

    routers = list(PROTECTED_ROUTERS)
    assert routers.index(ml_training.router) == routers.index(app_settings.router) + 1


def test_the_assembled_app_serves_both_paths():
    from app.main import app

    paths = {(getattr(r, "path", None), tuple(sorted(getattr(r, "methods", ()) or ()))) for r in app.routes}
    assert ("/api/v1/settings/ai/ml/status", ("GET",)) in paths
    assert ("/api/v1/settings/ai/ml/retrain", ("POST",)) in paths
