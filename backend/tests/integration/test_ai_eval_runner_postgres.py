"""The candidate replay on real PostgreSQL (W16 AGT-09 b, contract §2.1 test 3).

REAL rows end to end: a JWT admin queues a candidate through
``POST /api/v1/ai-eval/runs/evaluate/{dataset_id}`` (202); the route's
``apply_async`` payload is CAPTURED and bound to the Celery task's own
signature, then the task body runs in-process (``run_async`` refuses a running
loop, so the coroutine the task runs is awaited); items are committed one by
one (a second session sees item 1 before item 2 runs) -> ``completed``. A
second dispatch of the same id replays nothing. A run killed after two items
(``asyncio.CancelledError``) stays ``running`` until ``reconcile_stale_runs``
settles it ``partial`` / ``worker_lost`` with the two items aggregated. The
completed run becomes the baseline; a worse candidate (an ML model that
answers UNKNOWN) on the same dataset is gated ``fail`` on ``no_regression``.
A second test times the reconcile sweep over 1,000 silent runs.

Uses ``tests/integration/defect_w06_world.py`` (it awaits
``dispose_engine_for_loop()`` before building its engine and after disposing
it); this module's fixture also closes the app's Redis / Mongo clients.
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head and ``REDIS_URL``.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete, func, select

from tests.integration.defect_w06_world import defect_world, env

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def world(monkeypatch):
    env("REDIS_URL")
    from app.db.mongo import close_mongo
    from app.db.redis_client import close_redis
    from app.models.postgres import AIEvalBaseline, AIEvalDataset
    from app.services.golden_datasets import get_golden_classification_items
    from app.worker import tasks

    try:
        async with defect_world() as w:
            w.dataset_id = uuid.uuid4()
            w.agent_name = f"W16RunnerAgent{w.tag}"
            async with w.sessions.begin() as db:
                items = get_golden_classification_items()
                db.add(AIEvalDataset(id=w.dataset_id, name=f"w16c-golden-{w.tag}", task_type="classification",
                                     items=items, item_count=len(items), is_active=True))
            w.dispatched = []

            def _capture(*args: Any, **options: Any) -> None:
                w.dispatched.append(options)

            monkeypatch.setattr(tasks.run_candidate_evaluation, "apply_async", _capture)
            try:
                yield w
            finally:
                async with w.sessions.begin() as db:
                    await db.execute(delete(AIEvalBaseline).where(AIEvalBaseline.agent_name == w.agent_name))
                    await db.execute(delete(AIEvalDataset).where(AIEvalDataset.id == w.dataset_id))
    finally:
        await close_redis()
        await close_mongo()


async def _queue(w: SimpleNamespace, tier: str) -> uuid.UUID:
    response = await w.client.post(
        f"/api/v1/ai-eval/runs/evaluate/{w.dataset_id}",
        json={"candidate": {"tier": tier}, "project_id": str(w.P)}, headers=w.admin,
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    return uuid.UUID(response.json()["run_id"])


async def _execute_dispatched(w: SimpleNamespace) -> dict:
    """The captured payload, bound to the REAL task signature, then the task's coroutine."""
    from app.worker import tasks

    options = w.dispatched.pop()
    assert options["queue"] == "ai_analysis"
    bound = inspect.signature(tasks.run_candidate_evaluation.run).bind(**options["kwargs"])
    return await tasks._run_candidate_evaluation_async(uuid.UUID(bound.arguments["run_id"]))


async def _stored(w: SimpleNamespace, run_id: uuid.UUID) -> int:
    from app.models.postgres import AIEvalItemOutput

    async with w.sessions() as other:
        return int((await other.execute(
            select(func.count()).select_from(AIEvalItemOutput).where(AIEvalItemOutput.eval_run_id == run_id)
        )).scalar_one())


async def _run_row(w: SimpleNamespace, run_id: uuid.UUID):
    from app.models.postgres import AIEvalRun

    async with w.sessions() as db:
        return (await db.execute(select(AIEvalRun).where(AIEvalRun.id == run_id))).scalar_one()


def _unknown_model(directory) -> None:
    """A throwaway ML model that answers UNKNOWN for everything: the "worse" candidate."""
    joblib = pytest.importorskip("joblib")
    dummy = pytest.importorskip("sklearn.dummy")
    from app.services.ml.classifier import CATEGORY_LABELS
    from app.services.ml.feature_extractor import FEATURE_NAMES

    rows = [[0.0] * len(FEATURE_NAMES) for _ in CATEGORY_LABELS]
    model = dummy.DummyClassifier(strategy="constant", constant=5).fit(rows, list(range(len(CATEGORY_LABELS))))
    joblib.dump(model, directory / "classifier_v1.joblib")
    (directory / "training_metadata.json").write_text(json.dumps({
        "status": "trained", "feature_names": FEATURE_NAMES, "category_labels": CATEGORY_LABELS,
    }))


async def test_a_replay_commits_item_by_item_settles_and_gates(world, monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services import ai_eval_runner as runner
    from app.services.ml import classifier

    w = world
    original = runner.replay_item
    seen: dict[int, int] = {}
    kill: dict[str, Any] = {"run": None}

    async def spy(**kw: Any):
        if kw["index"] in (1, 2):
            seen[kw["index"]] = await _stored(w, kw["run_id"])
        if kill["run"] == kw["run_id"] and kw["index"] == 2:
            raise asyncio.CancelledError()  # the worker goes away mid-run
        return await original(**kw)

    monkeypatch.setattr(runner, "replay_item", spy)

    # 1. queued -> running -> completed, one commit per item.
    first = await _queue(w, "rules")
    redelivery = dict(w.dispatched[-1])
    outcome = await _execute_dispatched(w)
    assert outcome == {"status": "completed", "run_id": str(first), "replayed": 12}
    assert seen == {1: 1, 2: 2}  # another session saw item 0 before item 1 ran, items 0-1 before item 2
    row = await _run_row(w, first)
    assert (row.status, row.metric_kind, row.total_items) == ("completed", "replay", 12)
    assert (row.scored_items, row.unscored_items, row.error_items, row.correct_items) == (12, 0, 0, 7)
    assert row.accuracy == pytest.approx(0.5833, abs=1e-4)
    assert {m["name"] for m in row.metrics} >= {"accuracy", "precision", "recall", "f1_score"}
    assert row.completed_at is not None and row.started_at is not None

    # 2. a second dispatch of the same id replays nothing.
    w.dispatched.append(redelivery)
    assert await _execute_dispatched(w) == {"skipped": "not_queued", "run_id": str(first)}
    assert await _stored(w, first) == 12

    # 3. killed after two items -> stays running -> the sweep settles it partial.
    second = await _queue(w, "rules")
    kill["run"] = second
    with pytest.raises(asyncio.CancelledError):
        await _execute_dispatched(w)
    lost = await _run_row(w, second)
    assert lost.status == "running" and await _stored(w, second) == 2
    async with w.sessions() as db:
        await runner.reconcile_stale_runs(db, now=lost.heartbeat_at + timedelta(seconds=899))
        await db.commit()
    assert (await _run_row(w, second)).status == "running"  # 899 s of silence is not yet lost
    async with w.sessions() as db:
        await runner.reconcile_stale_runs(db, now=lost.heartbeat_at + timedelta(seconds=901))
        await db.commit()
    settled = await _run_row(w, second)
    assert (settled.status, settled.error_code) == ("partial", "worker_lost")
    assert (settled.scored_items, settled.total_items) == (2, 12)
    assert settled.accuracy is not None  # the two stored items, aggregated

    # 4. the completed run is the baseline; a worse candidate fails the gate.
    baseline = await w.client.post(
        "/api/v1/ai-eval/baselines",
        json={"task_type": "classification", "agent_name": w.agent_name, "eval_run_id": str(first)},
        headers=w.admin,
    )
    assert baseline.status_code == 201, baseline.text
    _unknown_model(tmp_path)
    monkeypatch.setattr(settings, "ML_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(classifier, "_model", None)
    worse = await _queue(w, "ml")
    assert (await _execute_dispatched(w))["status"] == "completed"
    assert (await _run_row(w, worse)).accuracy == 0.0
    gate = await w.client.post(
        "/api/v1/ai-eval/pre-release-gate",
        json={"task_type": "classification", "agent_name": w.agent_name, "candidate_run_id": str(worse)},
        headers=w.admin,
    )
    assert gate.status_code == 200, gate.text
    verdict = gate.json()
    assert verdict["status"] == "fail"
    assert verdict["baseline_run_id"] == str(first)
    rules = {r["rule"]: r["passed"] for r in verdict["rule_results"]}
    assert rules["no_regression"] is False


async def test_the_reconcile_sweep_settles_a_thousand_silent_runs(world):
    from app.models.postgres import AIEvalItemOutput, AIEvalRun
    from app.worker import tasks

    w = world
    async with w.sessions.begin() as db:
        heartbeat = (await db.execute(select(func.now()))).scalar_one() - timedelta(minutes=16)
        runs = [uuid.uuid4() for _ in range(1000)]
        await db.execute(AIEvalRun.__table__.insert(), [
            {"id": rid, "dataset_id": w.dataset_id, "model_name": "rules", "task_type": "classification",
             "metric_kind": "replay", "status": "running", "project_id": w.P, "total_items": 12,
             "started_at": heartbeat, "heartbeat_at": heartbeat, "candidate": {"tier": "rules"}}
            for rid in runs
        ])
        await db.execute(AIEvalItemOutput.__table__.insert(), [
            {"id": uuid.uuid4(), "eval_run_id": rid, "item_index": 0, "status": "correct",
             "expected": {"failure_category": "FLAKY"}, "score": {"expected": "FLAKY", "predicted": "FLAKY"}}
            for rid in runs
        ])

    started = time.perf_counter()
    outcome = await tasks._reconcile_stale_eval_runs_async(heartbeat + timedelta(minutes=16))
    elapsed = time.perf_counter() - started
    print(f"reconcile of 1,000 silent runs: {elapsed:.2f} s ({outcome})")

    async with w.sessions() as db:
        statuses = (await db.execute(
            select(AIEvalRun.status, func.count()).where(AIEvalRun.id.in_(runs)).group_by(AIEvalRun.status)
        )).all()
    assert dict(statuses) == {"partial": 1000}
    assert outcome["settled"] >= 1000
