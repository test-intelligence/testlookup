"""Exercise the real async queue, without a network database or live writes."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from prometheus_client import REGISTRY
import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DisconnectionError, TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.db.pool_metrics import InstrumentedAsyncAdaptedQueuePool

pytestmark = pytest.mark.regression


def sample(name: str, *, role: str = "api", **labels: str) -> float:
    value = REGISTRY.get_sample_value(f"sqlalchemy_pool_{name}", {"role": role, **labels})
    assert value is not None, (name, labels)
    return value


def engine(*, size: int = 1, overflow: int = 1, timeout: float = 0.04):
    return create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=InstrumentedAsyncAdaptedQueuePool,
        pool_size=size,
        max_overflow=overflow,
        pool_timeout=timeout,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["api", "worker"])
async def test_real_async_pool_counts_capacity_overflow_timeout_and_recovery(monkeypatch, role):
    monkeypatch.setattr(settings, "PG_PROCESS_ROLE", role)
    db = engine()
    try:
        assert sample("size", role=role) == 1
        assert sample("max_overflow", role=role) == 1
        assert sample("capacity", role=role) == 2
        assert sample("capacity_limited", role=role) == 1
        assert sample("checked_out", role=role) == 0
        assert sample("overflow", role=role) == 0  # cold SQLAlchemy overflow is -1
        assert sample("utilization_ratio", role=role) == 0
        before = sample("timeouts_total", role=role)
        timed = sample("acquire_seconds_count", role=role, outcome="timeout")
        successful = sample("acquire_seconds_count", role=role, outcome="success")
        first = await db.connect()
        second = await db.connect()
        try:
            assert sample("checked_out", role=role) == db.sync_engine.pool.checkedout() == 2
            assert sample("overflow", role=role) == 1
            assert sample("utilization_ratio", role=role) == 1
            with pytest.raises(SQLAlchemyTimeoutError) as failure:
                await db.connect()
            assert failure.value.code == "3o7r"
            assert sample("timeouts_total", role=role) - before == 1
            assert sample("acquire_seconds_count", role=role, outcome="timeout") - timed == 1
            assert sample("acquire_seconds_count", role=role, outcome="success") - successful == 2
            assert sample("acquiring", role=role) == 0
            assert sample("checked_out", role=role) == 2
        finally:
            await first.close()
            assert sample("checked_out", role=role) == db.sync_engine.pool.checkedout() == 1
            await second.close()
        assert sample("checked_out", role=role) == 0
        assert sample("overflow", role=role) == 0
        assert sample("utilization_ratio", role=role) == 0
        async with db.connect() as recovered:
            assert (await recovered.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_waiting_and_cancelled_acquisition_leave_loop_and_pool_usable(monkeypatch):
    monkeypatch.setattr(settings, "PG_PROCESS_ROLE", "api")
    db = engine(overflow=0, timeout=1)
    held = await db.connect()

    async def acquire():
        return await db.connect()

    pending = asyncio.create_task(acquire())
    try:
        for _ in range(10):
            if sample("acquiring") == 1:
                break
            await asyncio.sleep(0)
        assert sample("acquiring") == 1
        before_timeouts = sample("timeouts_total")
        before_errors = sample("acquire_seconds_count", outcome="error")
        ticks = 0
        for _ in range(3):
            await asyncio.sleep(0.005)
            ticks += 1
        assert ticks == 3 and not pending.done()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert sample("acquiring") == 0
        assert sample("timeouts_total") == before_timeouts
        assert sample("acquire_seconds_count", outcome="error") - before_errors == 1
        assert sample("checked_out") == 1
        await held.close()
        async with db.connect() as recovered:
            assert (await recovered.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        pending.cancel()
        await held.close()
        await db.dispose()


@pytest.mark.asyncio
async def test_checkout_retry_is_one_logical_attempt_and_connections_are_reused(monkeypatch):
    monkeypatch.setattr(settings, "PG_PROCESS_ROLE", "api")
    db = engine()
    connects = []
    retries = []

    @event.listens_for(db.sync_engine, "connect")
    def connected(*_args):
        connects.append(1)

    @event.listens_for(db.sync_engine, "checkout")
    def disconnected_once(*_args):
        if not retries:
            retries.append(1)
            raise DisconnectionError("synthetic disconnect before successful checkout")

    before = sample("acquire_seconds_count", outcome="success")
    try:
        for _ in range(3):
            async with db.connect() as connection:
                assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
        assert len(connects) == 2  # one retry, then reuse the surviving connection
        assert sample("acquire_seconds_count", outcome="success") - before == 3
        assert sample("checked_out") == 0
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_other_sqlalchemy_timeout_is_an_error_not_pool_exhaustion(monkeypatch):
    monkeypatch.setattr(settings, "PG_PROCESS_ROLE", "api")
    db = engine()

    @event.listens_for(db.sync_engine, "checkout")
    def fail_checkout(*_args):
        raise SQLAlchemyTimeoutError("synthetic unrelated timeout")

    before_timeouts = sample("timeouts_total")
    before_errors = sample("acquire_seconds_count", outcome="error")
    try:
        with pytest.raises(SQLAlchemyTimeoutError, match="unrelated"):
            await db.connect()
        assert sample("timeouts_total") == before_timeouts
        assert sample("acquire_seconds_count", outcome="error") - before_errors == 1
        assert sample("checked_out") == db.sync_engine.pool.checkedout() == 0
        assert sample("acquiring") == 0
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_detached_pool_checkin_cannot_overwrite_replacement_pool(monkeypatch):
    monkeypatch.setattr(settings, "PG_PROCESS_ROLE", "api")
    db = engine()
    old = await db.connect()
    await db.dispose()
    assert sample("checked_out") == 0
    assert sample("capacity") == 2
    current = await db.connect()
    try:
        assert sample("checked_out") == 1
        await old.close()
        assert sample("checked_out") == 1
        await current.invalidate()
        assert sample("checked_out") == db.sync_engine.pool.checkedout() == 0
        assert sample("utilization_ratio") == 0
    finally:
        await old.close()
        await current.close()
        await db.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("size", "overflow"), [(0, 1), (1, -1)])
async def test_unbounded_configuration_has_no_false_finite_pressure(monkeypatch, size, overflow):
    monkeypatch.setattr(settings, "PG_PROCESS_ROLE", "api")
    db = engine(size=size, overflow=overflow)
    try:
        async with db.connect():
            assert sample("capacity") == 0
            assert sample("capacity_limited") == 0
            assert sample("utilization_ratio") == 0
    finally:
        await db.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_metric", ["gauge", "acquiring", "duration", "timeouts"])
async def test_metric_failures_preserve_success_and_original_timeout(monkeypatch, failed_metric, caplog):
    import app.db.pool_metrics as metrics

    monkeypatch.setattr(settings, "PG_PROCESS_ROLE", "api")

    class BrokenMetric:
        def labels(self, **_kwargs):
            raise OSError("synthetic unavailable metrics file")

    if failed_metric == "gauge":
        monkeypatch.setattr(metrics, "sqlalchemy_pool_checked_out", BrokenMetric())
    else:
        name = {"acquiring": "sqlalchemy_pool_acquiring", "duration": "sqlalchemy_pool_acquire_seconds", "timeouts": "sqlalchemy_pool_timeouts_total"}[failed_metric]
        monkeypatch.setattr(metrics, name, BrokenMetric())
    db = engine(overflow=0)
    try:
        async with db.connect() as held:
            assert (await held.execute(text("SELECT 1"))).scalar_one() == 1
            with pytest.raises(SQLAlchemyTimeoutError) as failure:
                await db.connect()
            assert failure.value.code == "3o7r"
        assert sum(record.message == "db_pool_metric_update_failed" for record in caplog.records) == 1
    finally:
        await db.dispose()


@pytest.mark.parametrize("role", ["api", "worker"])
def test_real_multiprocess_totals_keep_worst_process_pressure_and_reap_dead_children(tmp_path, role):
    """One saturated child, one idle child: total utilization is only 50%."""
    metric_dir = tmp_path / "metrics"
    metric_dir.mkdir()
    env = os.environ | {
        "PROMETHEUS_MULTIPROC_DIR": str(metric_dir),
        "PG_PROCESS_ROLE": role,
        "PYTHONPATH": os.pathsep.join(filter(None, (
            str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH", "")
        ))),
    }
    emitter = """
import asyncio, os, sys
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from app.db.pool_metrics import InstrumentedAsyncAdaptedQueuePool
async def main():
    db = create_async_engine('sqlite+aiosqlite://', poolclass=InstrumentedAsyncAdaptedQueuePool,
                             pool_size=1, max_overflow=1)
    held = [await db.connect() for _ in range(int(sys.argv[1]))]
    Path(sys.argv[2]).write_text(str(os.getpid()), encoding='ascii')
    sys.stdin.readline()
    for connection in held:
        await connection.close()
    await db.dispose()
asyncio.run(main())
"""
    collector = """
import json
from prometheus_client import CollectorRegistry, multiprocess
registry = CollectorRegistry()
multiprocess.MultiProcessCollector(registry)
print(json.dumps({s.name + '|' + json.dumps(s.labels, sort_keys=True): s.value
                  for family in registry.collect() for s in family.samples
                  if s.name.startswith('sqlalchemy_pool_')}))
"""

    def scrape():
        result = subprocess.run([sys.executable, "-c", collector], env=env, capture_output=True,
                                text=True, check=True, timeout=15)
        return json.loads(result.stdout)

    def value(samples, name, **labels):
        return samples[f"sqlalchemy_pool_{name}|" + json.dumps({"role": role, **labels}, sort_keys=True)]

    children = []
    try:
        for index, held in enumerate((2, 0)):
            ready = tmp_path / f"ready-{index}"
            child = subprocess.Popen([sys.executable, "-c", emitter, str(held), str(ready)],
                                     env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
            children.append(child)
            deadline = time.monotonic() + 15
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists(), "synthetic async pool child did not initialize"
        samples = scrape()
        assert value(samples, "checked_out") == 2
        assert value(samples, "capacity") == 4
        assert value(samples, "size") == 2
        assert value(samples, "max_overflow") == 2
        assert value(samples, "overflow") == 1
        assert value(samples, "utilization_ratio") == 1
        assert value(samples, "acquire_seconds_count", outcome="success") == 2
        assert all("pid" not in json.loads(key.split("|", 1)[1]) for key in samples)
        children[0].kill()
        children[0].wait(timeout=10)
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(children[0].pid, path=str(metric_dir))
        surviving = scrape()
        assert value(surviving, "checked_out") == 0
        assert value(surviving, "capacity") == 2
        assert value(surviving, "utilization_ratio") == 0
        # Counters/histograms retain completed observations across child recycle.
        assert value(surviving, "acquire_seconds_count", outcome="success") == 2
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)


def test_application_cached_engine_uses_instrumented_async_pool():
    from app.db.postgres import get_engine

    db = get_engine()
    assert isinstance(db.sync_engine.pool, InstrumentedAsyncAdaptedQueuePool)
    assert get_engine() is db


def test_pool_dashboard_preserves_target_pressure_and_scoped_timeout_totals():
    source = Path(__file__).resolve().parents[3] / "infra/monitoring/grafana/dashboards/testlookup-overview.json"
    panels = {panel["id"]: panel for panel in json.loads(source.read_text(encoding="utf-8"))["panels"]}
    assert panels[110]["type"] == "row" and panels[110]["collapsed"] is False
    assert panels[110]["gridPos"]["y"] < panels[111]["gridPos"]["y"]
    pressure = panels[111]["targets"][0]["expr"]
    assert "max by (cluster, namespace, job, instance, role)" in pressure
    assert "sqlalchemy_pool_utilization_ratio" in pressure
    assert 'sqlalchemy_pool_capacity_limited{job=~"testlookup-backend|testlookup-worker"} > 0' in pressure
    assert " / " not in pressure
    assert panels[111]["fieldConfig"]["defaults"]["unit"] == "percentunit"
    timeouts = panels[112]["targets"][0]["expr"]
    assert "sum by (cluster, namespace, role)" in timeouts
    assert "increase(sqlalchemy_pool_timeouts_total" in timeouts and "[5m]" in timeouts
    for panel_id in (111, 112):
        assert 'role=~"api|worker"' in panels[panel_id]["targets"][0]["expr"]
        assert 'job=~"testlookup-backend|testlookup-worker"' in panels[panel_id]["targets"][0]["expr"]
        assert '"noValue"' not in json.dumps(panels[panel_id])
