"""Decision outbox admission must fit in one real pooled SQL connection."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.agents import anomaly_agent, base


@pytest.mark.parametrize(
    "baseline,current,chosen",
    [(None, 80.0, "no_baseline"), (95.0, 80.0, "regression"), (95.0, 93.0, "within_tolerance")],
)
async def test_anomaly_decision_releases_read_connection_and_preserves_baseline_facts(monkeypatch, baseline, current, chosen):
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=AsyncAdaptedQueuePool,
        pool_size=1, max_overflow=0, pool_timeout=0.05,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    metadata = MetaData()
    outbox = Table("decision_admission_fixture", metadata, Column("id", Integer, primary_key=True), Column("detail", String))
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)

    agent = anomaly_agent.AnomalyDetectionAgent()
    for method in ("mark_stage_running", "mark_stage_done", "broadcast_progress"):
        monkeypatch.setattr(agent, method, AsyncMock())
    monkeypatch.setattr(anomaly_agent, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(anomaly_agent.settings, "ANOMALY_REGRESSION_THRESHOLD", 10.0)
    reads = []
    prior = ["prior-most-recent", "prior-older", "prior-oldest"] if baseline is not None else []

    async def read(db, label):
        await db.execute(text("SELECT 1"))
        assert engine.pool.checkedout() == 1
        reads.append(label)

    async def baseline_read(db, *_args):
        await read(db, "baseline")
        return baseline, prior, prior[0] if prior else None

    async def failure_read(db, _current_id, previous_id):
        assert previous_id == prior[0]
        await read(db, "failure_diff")
        return {"new": [], "reopened": [], "persistent": []}

    async def performance_read(db, *_args):
        await read(db, "performance")
        return []

    async def flaky_read(db, *_args):
        await read(db, "flaky")
        return []

    # Keep the real BaseAgent.log_decision, including its independent emit
    # boundary; only substitute the external outbox schema/admission operation.
    async def admit(_pipeline_run_id, event_type, **kwargs):
        assert event_type == "decision_made"
        assert engine.pool.checkedout() == 0, "a read connection is still held before independent decision admission"
        async with sessions() as writer:
            await writer.execute(insert(outbox).values(detail=json.dumps(kwargs["detail"])))
            await writer.commit()

    monkeypatch.setattr(agent, "_get_baseline_pass_rate", baseline_read)
    monkeypatch.setattr(agent, "_classify_failure_diff", failure_read)
    monkeypatch.setattr(agent, "_detect_perf_anomalies", performance_read)
    monkeypatch.setattr(agent, "_find_flaky_tests_in_run", flaky_read)
    monkeypatch.setattr(base, "emit_event", admit)
    try:
        result = await asyncio.wait_for(agent.run({
            "pipeline_run_id": "11111111-1111-4111-8111-111111111111",
            "test_run_id": "22222222-2222-4222-8222-222222222222",
            "project_id": "33333333-3333-4333-8333-333333333333",
            "pass_rate": current, "total_tests": 20, "branch": "main",
            "_cost_budget_mode_override": "rules",
        }), timeout=2)
        assert result["errors"] == []
        assert result["is_regression"] is (chosen == "regression")
        assert engine.pool.checkedout() == 0
        async with sessions() as reader:
            rows = (await reader.execute(select(outbox.c.detail))).scalars().all()
        assert len(rows) == 1
        decision = json.loads(rows[0])
        assert decision["chosen"] == chosen
        if baseline is None:
            assert decision["decision_point"] == "baseline_availability"
            assert reads == ["baseline", "performance", "flaky"]
        else:
            assert decision["decision_point"] == "pass_rate_regression"
            assert decision["context"]["baseline_pass_rate"] == 95.0
            assert decision["context"]["sample_size"] == 3
            assert decision["context"]["baseline_type"] == "branch=main"
            assert decision["context"]["drop"] == round(95.0 - current, 2)
            assert reads == ["baseline", "failure_diff", "performance", "flaky"]
            if chosen == "regression":
                anomaly, = result["anomalies"]
                assert anomaly["baseline_run_id"] == prior[0]
                assert anomaly["sample_size"] == 3 and anomaly["value"] == 15.0
        agent.mark_stage_done.assert_awaited_once()
        assert "error" not in agent.mark_stage_done.await_args.kwargs
    finally:
        await engine.dispose()
